import os, sys, subprocess
from pathlib import Path
import yaml

def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)

def is_dirty(repo: Path) -> bool:
    return subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate.py repos.yml")

    token = os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("AGG_PAT is required")

    cfg_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = yaml.safe_load(cfg_text) or {}
    if not isinstance(data, dict):
        raise SystemExit("repos.yml top level must be a mapping")

    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append(k.replace("./", ""))  # dir name only

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # token をURLに埋めない（ログ漏れしにくい）
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "https://github.com/")
    run("git", "config", "--global",
    "url.https://github.com/.insteadOf", "git@github.com:")
    run("git", "config", "--global",
    "url.https://github.com/.insteadOf", "ssh://git@github.com/")
    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # 1) gitaggregate を最初に走らせる（必要な repo は work 配下に揃う）
    (work / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 2) submodule を持たない repo を先に push（kns-oca / kns-custom 等）
    for d in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

    # 3) submodule を持つ repo（kns-private 等）は最後に submodule update → commit → push
    for d in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        run("git", "submodule", "update", "--init", "--remote", cwd=repo)

        if is_dirty(repo):
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)

        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

if __name__ == "__main__":
    main()
