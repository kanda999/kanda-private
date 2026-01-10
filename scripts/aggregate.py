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
    data = yaml.safe_load(cfg)

    # repos.yml の top-level key（例: ./kns-oca）をそのまま使う
    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append((k.replace("./", ""), origin))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # token をURLに埋めない方式（ログ漏れしにくい）
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "https://github.com/")
    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # clone → aggregate → push
    for d, url in repos:
        run("git", "clone", url, str(work / d))

    (work / "repos.yml").write_text(cfg, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    for d, _ in repos:
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=work / d)

    # submodule がある repo だけ update & commit
    for d, _ in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue
        run("git", "submodule", "update", "--init", "--remote", cwd=repo)
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0
        if dirty:
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

if __name__ == "__main__":
    main()
