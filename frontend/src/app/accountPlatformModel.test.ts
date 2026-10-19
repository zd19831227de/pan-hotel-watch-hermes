import assert from "node:assert/strict";
import test from "node:test";

import {
  canBindToCtripCompetitorWatch,
  canBindToCtripDataCenter,
  defaultAccountFormForPlatform,
  platformLabel,
} from "./accountPlatformModel.ts";
import type { BrowserSession } from "../services/browserSessionsApi.ts";

const sessions: BrowserSession[] = [
  session("ctrip-ebk", "ctrip", "ebk"),
  session("ctrip-public", "ctrip", "competitor_price"),
  session("meituan-ebk", "meituan", "ebk"),
  session("fliggy-ebk", "fliggy", "ebk"),
];

test("Ctrip bindings exclude Meituan and Fliggy sessions", () => {
  assert.equal(canBindToCtripDataCenter(sessions[0]), true);
  assert.equal(canBindToCtripCompetitorWatch(sessions[1]), true);
  assert.equal(canBindToCtripDataCenter(sessions[2]), false);
  assert.equal(canBindToCtripCompetitorWatch(sessions[2]), false);
  assert.equal(canBindToCtripDataCenter(sessions[3]), false);
  assert.equal(canBindToCtripCompetitorWatch(sessions[3]), false);
});

test("Meituan account defaults are read-only EBK", () => {
  assert.deepEqual(defaultAccountFormForPlatform("meituan"), {
    platform: "meituan",
    intended_use: "ebk",
    connection_mode: "unknown",
    session_port: "9521",
  });
  assert.equal(platformLabel("meituan"), "美团");
});

test("Fliggy account defaults are read-only EBK", () => {
  assert.deepEqual(defaultAccountFormForPlatform("fliggy"), {
    platform: "fliggy",
    intended_use: "ebk",
    connection_mode: "unknown",
    session_port: "9531",
  });
  assert.equal(platformLabel("fliggy"), "飞猪");
});

function session(
  platform_account_id: string,
  platform: string,
  intended_use: BrowserSession["intended_use"],
): BrowserSession {
  return {
    browser_session_id: `browser-${platform_account_id}`,
    platform_account_id,
    display_name: platform_account_id,
    platform,
    session_port: 9521,
    intended_use,
    connection_mode: "unknown",
    can_auto_write_price: false,
    login_state: "logged_out",
    human_verification_status: "required",
    last_heartbeat_at: "2026-07-09T00:00:00+08:00",
    last_action: "Test session.",
    risk_flags: [],
  };
}
