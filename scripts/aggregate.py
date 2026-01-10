import os, sys, subprocess
from pathlib import Path
import yaml

def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate.py repos.yml")

    token = os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("AGG_PAT is required")

    cfg = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = yaml.safe_load(cfg) or {}

    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append((k.replace("./", ""), origin))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # 非対話（認証が効いてないときに Username を聞きに行って落ちるのを早めに止める）
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # https は token付きへ
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "https://github.com/")

    # ★重要：SSH 形式も token付き https に変換（submodule clone 対策）
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "git@github.com:")
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "ssh://git@github.com/")

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # clone → aggregate
    for d, url in repos:
        run("git", "clone", url, str(work / d))

    (work / "repos.yml").write_text(cfg, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 1) submodule を持たない repo を先に push（依存先を先に更新）
    for d, _ in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

    # 2) submodule を持つ repo は最後に update → commit → push（1回で済む）
    for d, _ in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        run("git", "submodule", "update", "--init", "--remote", cwd=repo)

        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0
        if dirty:
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)

        # dirty でなくても aggregate 結果が変わっている可能性があるので push はする
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

if __name__ == "__main__":
    main()
