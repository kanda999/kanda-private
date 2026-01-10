import os, sys, subprocess
from pathlib import Path
import yaml

def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)

def sh(*cmd, cwd=None) -> str:
    return subprocess.check_output(list(cmd), cwd=cwd, text=True).strip()

def is_dirty(repo: Path) -> bool:
    return subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0

def set_token_remote(repo: Path, token: str):
    """origin を必ず token付き https にする（push時の Username 要求を根絶）"""
    url = sh("git", "remote", "get-url", "origin", cwd=repo)

    # SSH -> HTTPS
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/"):]

    # HTTPS -> token付き HTTPS
    if url.startswith("https://github.com/"):
        url = url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)

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

    # 対象 repo を repos.yml から抽出（dir名だけ）
    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append(k.replace("./", ""))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # 非対話を強制（認証が効かない場合に変な待ちが発生しない）
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # submodule の SSH URL を HTTPS として扱えるようにする（clone/update のため）
    # ※ここは token を直接含めず https://github.com に寄せる
    run("git", "config", "--global", "url.https://github.com/.insteadOf", "git@github.com:")
    run("git", "config", "--global", "url.https://github.com/.insteadOf", "ssh://git@github.com/")

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # 1) gitaggregate 実行（必要な repo は work 配下に揃う）
    (work / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 2) submodule を持たない repo を先に push
    for d in repos:
        repo = work / d
        if (repo / ".gitmodules").exists():
            continue
        if do_push:
            set_token_remote(repo, token)
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)
        else:
            print(f"[DRY] skip push: {d}")

    # 3) submodule repo は最後に submodule update → commit → push
    for d in repos:
        repo = work / d
        if not (repo / ".gitmodules").exists():
            continue

        if do_push:
            # submodule の clone も確実に token を使わせる（ここが肝）
            set_token_remote(repo, token)

        run("git", "submodule", "sync", "--recursive", cwd=repo)
        run("git", "submodule", "update", "--init", "--remote", cwd=repo)

        if is_dirty(repo):
            run("git", "add", "-A", cwd=repo)
            run("git", "commit", "-m", "update submodule", cwd=repo)

        if do_push:
            run("git", "push", "origin", "HEAD:_git_aggregated", "--force-with-lease", cwd=repo)
        else:
            print(f"[DRY] skip push: {d}")

if __name__ == "__main__":
    main()
