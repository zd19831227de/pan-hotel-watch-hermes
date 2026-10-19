#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按飞书消息 ID 取回消息里的原始附件（Excel / Word / PDF / 图片），供 read_file / vision_analyze 读内容。

用法：
    python fetch_feishu_message_file.py <message_id>            # 下载附件到当前目录
    python fetch_feishu_message_file.py <message_id> --list      # 只列出附件文件名与 file_key
    python fetch_feishu_message_file.py <message_id> --out-dir ~/tmp
    python fetch_feishu_message_file.py <message_id> --env /path/to/.env

凭证来源（按优先级）：
    1) 环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET
    2) --env 指定的 .env
    3) $HERMES_PROFILE / $HERMES_PROFILE_NAME 对应的
       ~/AppData/Local/hermes/profiles/<profile>/.env
    4) ~/AppData/Local/hermes/profiles/*/.env 里第一个含 FEISHU_APP_ID 的（多于一个时打印警告）

被回复消息的 message_id：看 <profile>/group-archive-spool/*.json 里
event.message.root_id / parent_id。
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request

DOMAINS = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}


def read_env_file(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def find_env(explicit=None):
    if explicit:
        return read_env_file(explicit)
    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        return {k: os.environ[k] for k in
                ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DOMAIN") if os.environ.get(k)}
    profile = os.environ.get("HERMES_PROFILE") or os.environ.get("HERMES_PROFILE_NAME")
    roots = [os.path.expanduser("~/.hermes/profiles"), os.path.expanduser("~/AppData/Local/hermes/profiles")]
    if profile:
        for root in roots:
            env = read_env_file(os.path.join(root, profile, ".env"))
            if env.get("FEISHU_APP_ID"):
                return env
    hits = []
    for root in roots:
        for p in sorted(glob.glob(os.path.join(root, "*", ".env"))):
            env = read_env_file(p)
            if env.get("FEISHU_APP_ID"):
                hits.append((p, env))
    if not hits:
        sys.exit("找不到含 FEISHU_APP_ID 的 .env；用 --env 指定或设环境变量。")
    if len(hits) > 1:
        sys.stderr.write("警告：发现多个 profile 的 .env，使用 %s（可用 --env 指定）\n" % hits[0][0])
    return hits[0][1]


def call(url, token=None, payload=None, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        sys.exit("HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "ignore")[:400]))
    return body if raw else json.loads(body.decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message_id")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--env")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    env = find_env(args.env)
    app_id, secret = env.get("FEISHU_APP_ID"), env.get("FEISHU_APP_SECRET")
    if not app_id or not secret:
        sys.exit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET。")
    # FEISHU_DOMAIN 是选择器（feishu/lark），不是 URL
    base = DOMAINS.get((env.get("FEISHU_DOMAIN") or "feishu").strip().lower(), DOMAINS["feishu"])

    tok = call(base + "/open-apis/auth/v3/tenant_access_token/internal",
               payload={"app_id": app_id, "app_secret": secret})
    if tok.get("code") != 0:
        sys.exit("取 token 失败：%s" % tok.get("msg"))
    token = tok["tenant_access_token"]

    msg = call("%s/open-apis/im/v1/messages/%s" % (base, args.message_id), token=token)
    if msg.get("code") != 0:
        sys.exit("读消息失败：%s（消息可能不属于本应用）" % msg.get("msg"))
    items = (msg.get("data") or {}).get("items") or []
    if not items:
        sys.exit("该消息没有内容（可能是普通文本，或在 root_id / parent_id 指向的那条上）。")

    for item in items:
        mtype = item.get("msg_type")
        try:
            content = json.loads((item.get("body") or {}).get("content") or "{}")
        except ValueError:
            content = {}
        key = content.get("file_key") or content.get("image_key")
        name = content.get("file_name") or ("%s_%s" % (mtype, key))
        if not key:
            print("跳过 %s 消息：没有 file_key/image_key" % mtype)
            continue
        kind = "file" if content.get("file_key") else "image"
        if args.list:
            print("%s\t%s\t%s" % (kind, name, key))
            continue
        os.makedirs(os.path.abspath(args.out_dir), exist_ok=True)
        dest = os.path.join(os.path.abspath(args.out_dir), name)
        blob = call("%s/open-apis/im/v1/messages/%s/resources/%s?type=%s"
                    % (base, args.message_id, key, kind), token=token, raw=True)
        with open(dest, "wb") as fh:
            fh.write(blob)
        print("saved\t%s\t%d bytes" % (dest, len(blob)))


if __name__ == "__main__":
    main()
