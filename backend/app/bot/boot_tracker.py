import json
import logging
from pathlib import Path
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


class BootTracker:
    def __init__(self, state_file: Path):
        self.state_file = self._normalize_state_file(state_file)

    def _normalize_state_file(self, state_file: Path) -> Path:
        # If the configured path is a mounted directory (common with bind mounts),
        # store the state inside it instead of treating it as a file directly.
        if state_file.exists() and state_file.is_dir():
            logger.warning(
                "State file path points to a directory; using an internal file instead: %s",
                state_file,
            )
            return state_file / "state.json"
        return state_file

    def _read_last_boot(self) -> Optional[float]:
        try:
            data = json.loads(self.state_file.read_text())
        except FileNotFoundError:
            return None
        except IsADirectoryError:
            logger.warning("State file path is a directory; resetting: %s", self.state_file)
            return None
        except json.JSONDecodeError:
            logger.warning("State file was corrupt, resetting: %s", self.state_file)
            return None

        return data.get("last_boot")

    def _write_boot(self, boot_time: float) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"last_boot": boot_time}, indent=2))
        except OSError as exc:
            logger.error("Failed to persist boot state: %s", exc)

    def should_notify_reboot(self) -> bool:
        current_boot = psutil.boot_time()
        previous_boot = self._read_last_boot()

        if previous_boot is None:
            self._write_boot(current_boot)
            return False

        has_rebooted = current_boot - previous_boot > 1
        if has_rebooted:
            self._write_boot(current_boot)
        return has_rebooted
