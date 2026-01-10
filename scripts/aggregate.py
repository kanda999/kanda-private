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
    origin を必ず token付きHTTPS に差し替える。
    - https://github.com/...     -> https://x-access-token:TOKEN@github.com/...
    - git@github.com:owner/repo  -> 上に変換
    - ssh://git@github.com/...   -> 上に変換
    これで push 時に Username を聞きに行って落ちる問題を確実に潰す。
    """
    url = sh("git", "remote", "get-url", "origin", cwd=repo)

    # SSH -> HTTPS
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/"):]

    # HTTPS -> token付きHTTPS
    if url.startswith("https://github.com/"):
        url = url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/",
            1,
        )

    run("git", "remote", "set-url", "origin", url, cwd=repo)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate.py repos.yml")

    token = os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("AGG_PAT is required")

    do_push = os.environ.get("DO_PUSH", "true").lower() == "true"

    cfg = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = yaml.safe_load(cfg) or {}
    if not isinstance(data, dict):
        raise SystemExit("repos.yml top level must be a mapping")

    # repos.yml の top-level key（例: ./knd-oca）をそのまま使う
    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append((k.replace("./", ""), origin))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # 非対話（認証が効かないときに Username を聞きに行って詰まらない）
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # 1) clone（ここではまだ push しない）
    for d, url in repos:
        run("git", "clone", url, str(work / d))

    # 2) aggregate
    (work / "repos.yml").write_text(cfg, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 3) submodule を持たない repo を先に push（submodule が参照する先を更新）
    for d, _ in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue

        if do_push:
            ensure_token_origin(repo, token)
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)
        else:
            print(f"[DRY] skip push: {d}")

    # 4) submodule repo（例: *-private）は最後に submodule update → commit → push（1回だけ）
    for d, _ in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        # submodule update が SSH URL でも、親 repo の origin を token付きにしておくと
        # private repo の参照や fetch がこけにくい（加えて URL は clone 時の設定に依存）
        if do_push:
            ensure_token_origin(repo, token)

        # submodule の URL が git@github.com: の場合でも、
        # 親 repo の global 設定ではなく、最も確実なのは submodule の origin 自体を HTTPS にすること。
        # ただし .gitmodules を改変して commit したくないので、submodule update は一旦そのまま実行し、
        # 失敗した場合に URL を https へ差し替えてリトライする。
        try:
            run("git", "submodule", "update", "--init", "--remote", cwd=repo)
        except subprocess.CalledProcessError:
            # submodule URL をローカル設定で HTTPS に上書きして再試行（コミットされない）
            # .gitmodules から URL を取って https に変換し、submodule.<name>.url を設定する
            try:
                out = sh("git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.url$", cwd=repo)
            except subprocess.CalledProcessError:
                raise

            for line in out.splitlines():
                key, url = line.split(None, 1)      # submodule.NAME.url URL
                name = key.split(".")[1]            # NAME
                # git@github.com:owner/repo.git -> https://github.com/owner/repo.git
                if url.startswith("git@github.com:"):
                    url = "https://github.com/" + url[len("git@github.com:"):]
                elif url.startswith("ssh://git@github.com/"):
                    url = "https://github.com/" + url[len("ssh://git@github.com/"):]
                run("git", "config", f"submodule.{name}.url", url, cwd=repo)

            run("git", "submodule", "sync", "--recursive", cwd=repo)
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
