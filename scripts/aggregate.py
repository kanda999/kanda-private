import os, sys, subprocess
from pathlib import Path
import yaml

def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)

def sh(*cmd, cwd=None) -> str:
    return subprocess.check_output(list(cmd), cwd=cwd, text=True).strip()

def is_dirty(repo: Path) -> bool:
    return subprocess.call(["git", "diff", "--quiet"], cwd=repo) != 0

def to_https(url: str) -> str:
    """git@github.com:... / ssh://git@github.com/... を https://github.com/... に正規化"""
    url = url.strip()
    if url.startswith("git@github.com:"):
        return "https://github.com/" + url[len("git@github.com:"):]
    if url.startswith("ssh://git@github.com/"):
        return "https://github.com/" + url[len("ssh://git@github.com/"):]
    return url

def set_token_remote(repo: Path, token: str):
    """origin を必ず token付き https にする（push時の Username 要求を根絶）"""
    url = sh("git", "remote", "get-url", "origin", cwd=repo)
    url = to_https(url)
    if url.startswith("https://github.com/"):
        url = url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)
    run("git", "remote", "set-url", "origin", url, cwd=repo)

def rewrite_submodule_urls_to_https(repo: Path):
    """
    .gitmodules の URL が SSH の場合でも、ローカル config で https に上書きして
    `git submodule update` が SSH を使わないようにする（コミットされない）
    """
    try:
        out = sh("git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.url$", cwd=repo)
    except subprocess.CalledProcessError:
        return  # .gitmodules が無い or URL 定義が無い

    for line in out.splitlines():
        key, url = line.split(None, 1)          # 例: submodule.knd-oca.url <url>
        name = key.split(".")[1]                # knd-oca
        run("git", "config", f"submodule.{name}.url", to_https(url), cwd=repo)

    run("git", "submodule", "sync", "--recursive", cwd=repo)

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

    # 対象 repo（dir名）を repos.yml から抽出
    repos = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            repos.append(k.replace("./", ""))

    work = Path("_work")
    run("rm", "-rf", str(work))
    work.mkdir()

    # 非対話を強制（認証が効かない場合に待たない）
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # https は token 付きに置換（pushやprivate cloneの認証を通す）
    run("git", "config", "--global",
        f"url.https://x-access-token:{token}@github.com/.insteadOf", "https://github.com/")

    # submodule の SSH URL を https 扱いにする（登録段階で https に寄せる）
    run("git", "config", "--global", "url.https://github.com/.insteadOf", "git@github.com:")
    run("git", "config", "--global", "url.https://github.com/.insteadOf", "ssh://git@github.com/")

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    # 1) まず gitaggregate を実行（work 配下に各 repo が揃う）
    (work / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=work)

    # 2) submodule を持たない repo を先に push（submodule 参照先を先に更新）
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
            set_token_remote(repo, token)

        # ★ここが今回の要点：SSH URL の submodule を https に上書きしてから update
        rewrite_submodule_urls_to_https(repo)

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
