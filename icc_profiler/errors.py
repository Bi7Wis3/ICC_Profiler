class PipelineError(Exception):
    """A pipeline stage could not run or did not produce the expected result.

    Always carries an actionable message -- what's missing/wrong and what to
    do about it -- rather than letting a raw subprocess/traceback surface.
    """


class ArgyllNotFoundError(PipelineError):
    """A required ArgyllCMS binary could not be located."""

    def __init__(self, tool_name: str, bin_dir: str | None = None):
        where = f"configured bin_dir {bin_dir!r}" if bin_dir else "PATH"
        super().__init__(
            f"ArgyllCMS tool '{tool_name}' not found on {where}. "
            f"Install ArgyllCMS (e.g. `sudo apt install argyll` on WSL/Ubuntu, "
            f"or download the official build for native Windows from "
            f"argyllcms.com) and either add it to PATH or set "
            f"argyll.bin_dir in the job config. Run `icc_profiler doctor` "
            f"to check resolution of all required tools."
        )
        self.tool_name = tool_name
