"""
剧本解析（增强版入口）
--------------------
对外保留与上游一致的接口 parse_novel_to_script(text, voice_map)，
内部使用逐句切分的增强算法（见 _parse_impl.py）。

调用方式（推荐，保证拿到增强版）：
    .venv/bin/python -m scripts.parse_script --input data/novel.txt --start 0 --end 200
    .venv/bin/python -m scripts.audiobook_generator --input ... --name 第001章
"""
from __future__ import annotations

import sys

if __package__ in (None, ""):
    # 直接 `python scripts/parse_script.py` 时，把仓库根目录加入 sys.path 以便包内导入
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts._parse_impl import (  # noqa: F401
        QUOTE_PAIRS,
        SPEECH_VERBS,
        main,
        parse_novel_to_script,
    )
else:
    from ._parse_impl import (  # noqa: F401
        QUOTE_PAIRS,
        SPEECH_VERBS,
        main,
        parse_novel_to_script,
    )

__all__ = ["parse_novel_to_script", "SPEECH_VERBS", "QUOTE_PAIRS", "main"]

if __name__ == "__main__":
    main()
