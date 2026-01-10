import os
import sys
import subprocess
from pathlib import Path
import yaml


def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)


def sh(*cmd, cwd=None) -> str:
    return subprocess.check_output(list(cmd), cwd=cwd, text=True).strip()


def is_dirty(repo: Path) -> bool:
    return subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0


def ensure_token_origin(repo: Path, token: str):
    """pushが Username を聞きに行かないように origin を token付きHTTPS にする"""
    url = sh("git", "remote", "get-url", "origin", cwd=repo)

    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/"):]

    if url.startswith("https://github.com/"):
        url = url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)

    run("git", "remote", "set-url", "origin", url, cwd=repo)


def configure_submodules_token_urls(repo: Path, token: str):
    """
    .gitmodules の SSH URL を、ローカル設定(.git/config)側で token付きHTTPS に上書きする。
    これにより `git submodule update` が SSH を使わなくなる。
    ※コミットはされない（workdir内だけ）
    """
    # .gitmodules から submodule.*.url を列挙
    out = sh("git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.url$", cwd=repo)
    for line in out.splitlines():
        key, url = line.split(None, 1)     # 例: submodule.knd-oca.url git@github.com:...
        name = key.split(".")[1]           # knd-oca

        url = url.strip()
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url[len("git@github.com:"):]
        elif url.startswith("ssh://git@github.com/"):
            url = "https://github.com/" + url[len("ssh://git@github.com/"):]

        if url.startswith("https://github.com/"):
            url = url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)

        # submodule.<name>.url をローカル config に上書き
        run("git", "config", f"submodule.{name}.url", url, cwd=repo)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate.py repos.yml")

    token = os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("AGG_PAT is required")

    cfg = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = yaml.safe_load(cfg) or {}
    if not isinstance(data, dict):
        raise SystemExit("repos.yml top level must be a mapping")

    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append((k.replace("./", ""), origin))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # clone → aggregate
    for d, url in repos:
        run("git", "clone", url, str(work / d))

    (work / "repos.yml").write_text(cfg, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 1) submodule を持たない repo を先に push
    for d, _ in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue
        ensure_token_origin(repo, token)
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)

    # 2) submodule repo は最後に submodule update → commit → push（1回）
    for d, _ in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        # ★ここが肝：submodule URL を token付きHTTPS に上書きしてから update
        configure_submodules_token_urls(repo, token)

        run("git", "submodule", "update", "--init", "--remote", cwd=repo)

        if is_dirty(repo):
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)

        ensure_token_origin(repo, token)
        run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)


if __name__ == "__main__":
    main()
