from __future__ import annotations

from pathlib import Path


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = self._scan()

    def descriptions(self) -> str:
        lines = []
        for name, skill in sorted(self.skills.items()):
            desc = skill["description"] or "No description."
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def render(self, names: list[str]) -> str:
        chunks = []
        for name in names:
            skill = self.skills.get(name)
            if skill:
                chunks.append(f'<skill name="{name}">\n{skill["body"]}\n</skill>')
        return "\n\n".join(chunks)

    def _scan(self) -> dict[str, dict[str, str]]:
        found = {}
        if not self.skills_dir.exists():
            return found
        for path in sorted(self.skills_dir.rglob("SKILL.md")):
            text = path.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name") or path.parent.name
            found[name] = {
                "description": meta.get("description", ""),
                "body": body.strip(),
            }
        return found

    def _parse_frontmatter(self, text: str) -> tuple[dict[str, str], str]:
        if not text.startswith("---"):
            return {}, text
        _, rest = text.split("---", 1)
        meta_text, body = rest.split("---", 1)
        meta: dict[str, str] = {}
        for line in meta_text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        return meta, body
