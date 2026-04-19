import sys
from pathlib import Path

_main_repo = Path("D:/vk/quant")
_worktree = Path("D:/vk/quant/.worktrees/research-agent")

sys.path.insert(0, str(_main_repo))

import quant
quant.__path__.append(str(_worktree / "quant"))
