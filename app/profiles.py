"""Compose versioned prompts from common, subject, and I/O files."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

PROMPTS = ROOT / "app" / "prompts"
SUBJECTS = {"math": "数学", "physics": "物理", "chemistry": "化学", "general": "其他"}
PROFILE_SPECS = {
    "auto": ("自动识别学科", "auto", ()),
    "math-current": ("数学 · 综合经验", "math", ("math_current.md",)),
    "math-compact": ("数学 · 精简交互实验", "math", ("math_current.md", "math_compact.md")),
    "math-interactive": ("数学 · 交互白板方案", "math", ("math_current.md", "math_interactive.md")),
    "math-workflow": ("数学 · 推导工作流方案", "math", ("math_current.md", "math_workflow.md")),
    "physics": ("物理 · 过程与受力", "physics", ("physics.md",)),
    "chemistry": ("化学 · 粒子与反应", "chemistry", ("chemistry.md",)),
    "general": ("通用 · 关系与步骤", "general", ()),
}
AUTO_SUBJECT_FILES = {
    "math": ("math_current.md",),
    "physics": ("physics.md",),
    "chemistry": ("chemistry.md",),
    "general": (),
}


@dataclass(frozen=True)
class PromptProfile:
    id: str
    name: str
    subject: str
    version: str
    generator_prompt: str
    reviewer_prompt: str


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def _assemble(profile_id: str, name: str, subject: str, files: tuple[str, ...],
              generator_mode: str = "vision", reviewer_mode: str = "vision") -> PromptProfile:
    generator_io = "io_format_text.md" if generator_mode == "ocr_text" else "io_format.md"
    reviewer_common = "reviewer_common_text.md" if reviewer_mode == "ocr_text" else "reviewer_common.md"
    generator = "\n\n".join([_read("common_visual.md"), *(_read(item) for item in files), _read(generator_io)])
    review_subject = subject if subject in SUBJECTS else "general"
    reviewer = "\n\n".join([_read(reviewer_common), _read(f"reviewer_{review_subject}.md"),
                             _read("reviewer_format.md")])
    version = hashlib.sha256((generator + "\n" + reviewer).encode("utf-8")).hexdigest()[:12]
    return PromptProfile(profile_id, name, subject, version, generator, reviewer)


def get_profile(profile_id: str, generator_mode: str = "vision", reviewer_mode: str = "vision") -> PromptProfile:
    if profile_id not in PROFILE_SPECS:
        raise ValueError("未知 Prompt 方案")
    if generator_mode not in {"vision", "ocr_text"} or reviewer_mode not in {"vision", "ocr_text"}:
        raise ValueError("未知输入方式")
    name, subject, files = PROFILE_SPECS[profile_id]
    return _assemble(profile_id, name, subject, files, generator_mode, reviewer_mode)


def get_routed_profile(subject: str, generator_mode: str = "vision", reviewer_mode: str = "vision") -> PromptProfile:
    if subject not in SUBJECTS:
        raise ValueError("未知题目学科")
    return _assemble("auto", f"自动分流 · {SUBJECTS[subject]}", subject, AUTO_SUBJECT_FILES[subject],
                     generator_mode, reviewer_mode)


def router_prompt() -> str:
    return _read("router.md")


def list_profiles() -> list[dict]:
    return [{"id": p.id, "name": p.name, "subject": p.subject, "version": p.version,
             "generator_prompt": p.generator_prompt, "reviewer_prompt": p.reviewer_prompt,
             "generator_text_prompt": get_profile(p.id, generator_mode="ocr_text").generator_prompt,
             "reviewer_text_prompt": get_profile(p.id, reviewer_mode="ocr_text").reviewer_prompt}
            for p in (get_profile(profile_id) for profile_id in PROFILE_SPECS)]
