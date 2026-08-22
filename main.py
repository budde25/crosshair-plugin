import os
import re
import subprocess
import decky

# The crosshair text is drawn at a fixed size regardless of resolution, so the
# nudge from the screen center is constant rather than proportional.
STYLE_CENTER_OFFSETS = {
    "box": (22, 20),
    "dot": (8, 20),
}


class Plugin:
    def __init__(self):
        self.current_offset_x = None
        self.current_offset_y = None
        self.current_style = "box"  # Track current crosshair style: "box" or "dot"

    def _resolution_from_drm(self):
        """Read the active mode of the enabled DRM connector (internal or docked display)."""
        drm_path = "/sys/class/drm"
        candidates = []

        for connector in os.listdir(drm_path):
            connector_path = os.path.join(drm_path, connector)
            modes_file = os.path.join(connector_path, "modes")
            status_file = os.path.join(connector_path, "status")

            if not os.path.exists(modes_file) or not os.path.exists(status_file):
                continue

            try:
                with open(status_file, "r") as f:
                    if f.read().strip() != "connected":
                        continue

                with open(modes_file, "r") as f:
                    mode = f.readline().strip()

                if not re.fullmatch(r"\d+x\d+", mode):
                    continue

                enabled = False
                enabled_file = os.path.join(connector_path, "enabled")
                if os.path.exists(enabled_file):
                    with open(enabled_file, "r") as f:
                        enabled = f.read().strip() == "enabled"

                width, height = (int(v) for v in mode.split("x"))
                candidates.append((enabled, width, height))
            except (OSError, ValueError):
                continue

        if not candidates:
            return None

        # Prefer a connector gamescope actually drives; otherwise take the largest.
        enabled_candidates = [c for c in candidates if c[0]]
        chosen = max(enabled_candidates or candidates, key=lambda c: c[1] * c[2])
        return chosen[1], chosen[2]

    def _resolution_from_xrandr(self):
        """Fall back to querying gamescope's X display."""
        for display in (os.environ.get("DISPLAY"), ":0", ":1"):
            if not display:
                continue
            try:
                output = subprocess.run(
                    ["xrandr", "--display", display, "--current"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue

            match = re.search(r"current (\d+) x (\d+)", output)
            if match:
                return int(match.group(1)), int(match.group(2))

        return None

    async def get_display_resolution(self) -> list:
        """Return [width, height] of the active display, defaulting to the Deck's panel."""
        for detector in (self._resolution_from_drm, self._resolution_from_xrandr):
            try:
                resolution = detector()
            except Exception as e:
                decky.logger.warning(
                    f"Resolution detection via {detector.__name__} failed: {e}"
                )
                continue

            if resolution:
                decky.logger.info(
                    f"Detected display resolution: {resolution[0]}x{resolution[1]}"
                )
                return [resolution[0], resolution[1]]

        decky.logger.warning(
            "Could not detect display resolution, falling back to 1280x800"
        )
        return [1280, 800]

    def find_mangohud_config_path(self):
        """Find the active MangoHUD config file path by looking for mangoapp process"""
        try:
            # Scan /proc for running processes
            for proc_dir in os.listdir("/proc"):
                if not proc_dir.isdigit():
                    continue

                proc_path = f"/proc/{proc_dir}"

                try:
                    # Read command line to find mangoapp process
                    with open(f"{proc_path}/cmdline", "r") as f:
                        cmdline = f.read()

                    if "mangoapp" in cmdline:
                        # Found mangoapp process, now get its environment variables
                        with open(f"{proc_path}/environ", "r") as f:
                            environ = f.read()

                        # Look for MANGOHUD_CONFIGFILE environment variable
                        for env_var in environ.split("\0"):
                            if env_var.startswith("MANGOHUD_CONFIGFILE="):
                                config_path = env_var.split("=", 1)[1]
                                decky.logger.info(
                                    f"Found MangoHUD config file: {config_path}"
                                )
                                return config_path

                except (FileNotFoundError, PermissionError, IOError):
                    # Skip processes we can't read
                    continue

            # If no mangoapp process found, try common config locations
            common_paths = [
                os.path.expanduser("~/.config/MangoHud/MangoHud.conf"),
                "/tmp/mangohud.conf",
                "/tmp/MangoHud.conf",
            ]

            for path in common_paths:
                if os.path.exists(path):
                    decky.logger.info(f"Using fallback MangoHUD config: {path}")
                    return path

            # Create a default config if none exists
            default_path = "/tmp/mangohud_crosshair.conf"
            decky.logger.info(
                f"No MangoHUD config found, creating default: {default_path}"
            )
            return default_path

        except Exception as e:
            decky.logger.error(f"Error finding MangoHUD config: {str(e)}")
            # Fallback to default location
            return "/tmp/mangohud_crosshair.conf"

    async def write_crosshair_config(
        self,
        custom_text_1: str,
        custom_text_2: str,
        custom_text_3: str,
        offset_x: int,
        offset_y: int,
    ):
        try:
            # Always find the current MangoHUD config path - don't cache it
            config_path = self.find_mangohud_config_path()

            # Build crosshair config - ONLY crosshair settings, nothing else
            crosshair_config = f"""legacy_layout=false
background_alpha=0
alpha=1
background_color=020202
text_color=FF007B
custom_text={custom_text_1}
custom_text={custom_text_2}
custom_text={custom_text_3}
offset_x={offset_x}
offset_y={offset_y}"""

            # Write ONLY the crosshair config, overwriting the entire file
            with open(config_path, "w") as f:
                f.write(crosshair_config)

            decky.logger.info(f"Crosshair configuration written to {config_path}")

        except Exception as e:
            decky.logger.error(f"Error in write_crosshair_config: {str(e)}")
            raise e

    async def make_crosshair(self, style: str = "box") -> list:
        """Place a crosshair at the center of the detected display."""
        if style not in STYLE_CENTER_OFFSETS:
            style = "box"

        width, height = await self.get_display_resolution()
        shift_x, shift_y = STYLE_CENTER_OFFSETS[style]

        self.current_style = style
        self.current_offset_x = width // 2 - shift_x
        self.current_offset_y = height // 2 - shift_y

        if style == "dot":
            await self.write_crosshair_config(
                " ", "+", " ", self.current_offset_x, self.current_offset_y
            )
        else:
            await self.write_crosshair_config(
                "-----", "| + |", "-----", self.current_offset_x, self.current_offset_y
            )

        return [width, height]

    async def make_800p_crosshair(self):
        self.current_offset_x = 618
        self.current_offset_y = 380
        self.current_style = "box"
        await self.write_crosshair_config(
            "-----", "| + |", "-----", self.current_offset_x, self.current_offset_y
        )

    async def make_1080p_crosshair(self):
        self.current_offset_x = 938
        self.current_offset_y = 520
        self.current_style = "box"
        await self.write_crosshair_config(
            "-----", "| + |", "-----", self.current_offset_x, self.current_offset_y
        )

    async def make_2160p_crosshair(self):
        self.current_offset_x = 1900
        self.current_offset_y = 1060
        self.current_style = "box"
        await self.write_crosshair_config(
            "-----", "| + |", "-----", self.current_offset_x, self.current_offset_y
        )

    async def make_800p_dot_crosshair(self):
        self.current_offset_x = 631
        self.current_offset_y = 380
        self.current_style = "dot"
        await self.write_crosshair_config(
            " ", "+", " ", self.current_offset_x, self.current_offset_y
        )

    async def make_1080p_dot_crosshair(self):
        self.current_offset_x = 952
        self.current_offset_y = 520
        self.current_style = "dot"
        await self.write_crosshair_config(
            " ", "+", " ", self.current_offset_x, self.current_offset_y
        )

    async def make_2160p_dot_crosshair(self):
        self.current_offset_x = 1913
        self.current_offset_y = 1060
        self.current_style = "dot"
        await self.write_crosshair_config(
            " ", "+", " ", self.current_offset_x, self.current_offset_y
        )

    async def remove_crosshair(self):
        self.current_offset_x = 0
        self.current_offset_y = 0
        await self.write_crosshair_config(
            "", "", "", self.current_offset_x, self.current_offset_y
        )

    async def adjust_crosshair_offset(self, x_delta: int, y_delta: int):
        if self.current_offset_x is None:
            self.current_offset_x = 640
        if self.current_offset_y is None:
            self.current_offset_y = 400

        self.current_offset_x += x_delta
        self.current_offset_y += y_delta

        # Use the appropriate crosshair style when adjusting
        if self.current_style == "dot":
            await self.write_crosshair_config(
                " ", "+", " ", self.current_offset_x, self.current_offset_y
            )
        else:  # box style
            await self.write_crosshair_config(
                "-----", "| + |", "-----", self.current_offset_x, self.current_offset_y
            )

    async def get_current_offsets(self) -> list:
        if self.current_offset_x is None:
            self.current_offset_x = 640
        if self.current_offset_y is None:
            self.current_offset_y = 400
        return [self.current_offset_x, self.current_offset_y]

    async def _main(self):
        decky.logger.info("Crosshair Plugin initialized!")

    async def _unload(self):
        decky.logger.info("Crosshair Plugin unloaded!")
