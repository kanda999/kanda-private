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
    """
    push が Username を聞きに行かないように、origin を必ず token付きHTTPS にする
    """
    url = sh("git", "remote", "get-url", "origin", cwd=repo)

    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/"):]

    if url.startswith("https://github.com/"):
        url = url.replace("https://github.com/",
                          f"https://x-access-token:{token}@github.com/", 1)

    run("git", "remote", "set-url", "origin", url, cwd=repo)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate.py repos.yml")

    token = os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("AGG_PAT is required")

    do_push = os.environ.get("DO_PUSH", "true").lower() == "true"

    cfg_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = yaml.safe_load(cfg_text) or {}
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

    # 対話プロンプトを禁止（認証が効いていないときに固まらない）
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # ★最重要：SSH/HTTPS をすべて “PAT付きHTTPS” に強制変換
    # submodule が git@github.com:... のままでも、clone/update が HTTPS+PAT で通るようになる
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "https://github.com/")
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "git@github.com:")
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "ssh://git@github.com/")

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # 1) clone
    for d, url in repos:
        run("git", "clone", url, str(work / d))

    # 2) aggregate
    (work / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 3) submodule を持たない repo を先に push
    for d, _ in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue
        if do_push:
            ensure_token_origin(repo, token)
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)
        else:
            print(f"[DRY] skip push: {d}")

    # 4) submodule repo は最後に update → commit → push（1回）
    for d, _ in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        # sync はしない（.gitmodules の SSH に戻されやすいので）
        run("git", "submodule", "update", "--init", "--remote", cwd=repo)

        if is_dirty(repo):
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)

        if do_push:
            ensure_token_origin(repo, token)
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)
        else:
            print(f"[DRY] skip push: {d}")


if __name__ == "__main__":
    main()
