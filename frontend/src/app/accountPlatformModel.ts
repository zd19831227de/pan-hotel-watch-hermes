import type { BrowserSession } from "../services/browserSessionsApi.ts";

export type AccountPlatform = "ctrip" | "meituan" | "fliggy" | "doubao" | "qwen";

export function platformLabel(platform: string): string {
  if (platform === "meituan") {
    return "美团";
  }
  if (platform === "fliggy") {
    return "飞猪";
  }
  if (platform === "ctrip") {
    return "携程";
  }
  return platform || "未知平台";
}

export function isReadonlyEbkPlatform(platform: string): boolean {
  return platform === "meituan" || platform === "fliggy";
}

export function isLlmChatPlatform(platform: string): boolean {
  return platform === "doubao" || platform === "qwen";
}

export function llmChatDefaultUrl(platform: string): string {
  return platform === "qwen" ? "https://chat.qianwen.ai/" : "https://www.doubao.com/chat/";
}

export function defaultAccountFormForPlatform(platform: AccountPlatform): {
  platform: AccountPlatform;
  intended_use: "ebk" | "competitor_price" | "llm_chat";
  connection_mode: "unknown" | "direct" | "non_direct";
  session_port: string;
} {
  if (platform === "meituan") {
    return {
      platform: "meituan",
      intended_use: "ebk",
      connection_mode: "unknown",
      session_port: "9521",
    };
  }
  if (platform === "fliggy") {
    return {
      platform: "fliggy",
      intended_use: "ebk",
      connection_mode: "unknown",
      session_port: "9531",
    };
  }
  if (platform === "doubao") {
    return {
      platform: "doubao",
      intended_use: "llm_chat",
      connection_mode: "unknown",
      session_port: "9541",
    };
  }
  if (platform === "qwen") {
    return {
      platform: "qwen",
      intended_use: "llm_chat",
      connection_mode: "unknown",
      session_port: "9542",
    };
  }
  return {
    platform: "ctrip",
    intended_use: "ebk",
    connection_mode: "unknown",
    session_port: "9221",
  };
}

export function canBindToCtripDataCenter(session: BrowserSession): boolean {
  return session.platform === "ctrip" && session.intended_use === "ebk";
}

export function canBindToCtripCompetitorWatch(session: BrowserSession): boolean {
  return session.platform === "ctrip" && session.intended_use === "competitor_price";
}
