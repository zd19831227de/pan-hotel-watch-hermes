import type { BrowserSession } from "../services/browserSessionsApi.ts";
import { isLlmChatPlatform } from "./accountPlatformModel.ts";
import type { Shop } from "../services/shopsApi.ts";

export const SHOP_PLATFORM_ORDER = ["ctrip", "meituan", "fliggy"] as const;
export type ShopPlatform = (typeof SHOP_PLATFORM_ORDER)[number];

export type ShopPlatformSlot = {
  platform: ShopPlatform;
  // 契约上每店每平台至多 1 个；数据异常时如实全部展示，不截断。
  sessions: BrowserSession[];
};

export type ShopTreeNode = {
  shop: Shop;
  platforms: ShopPlatformSlot[];
};

export type AccountTree = {
  // 竞品账号是全局区，固定挂在树的第一个节点，不归属任何店铺。
  competitorAccounts: BrowserSession[];
  shopNodes: ShopTreeNode[];
  // 未分组 = EBK 账号无 shop_key，或 shop_key 指向已不存在的店铺。
  unassignedSessions: BrowserSession[];
};

export function buildAccountTree(shops: Shop[], sessions: BrowserSession[]): AccountTree {
  const businessSessions = sessions.filter((session) => !isLlmChatPlatform(session.platform));
  const competitorAccounts = businessSessions.filter(
    (session) => session.intended_use === "competitor_price",
  );
  const ebkSessions = businessSessions.filter((session) => session.intended_use === "ebk");
  const shopKeys = new Set(shops.map((shop) => shop.shop_key));

  const shopNodes: ShopTreeNode[] = shops.map((shop) => ({
    shop,
    platforms: SHOP_PLATFORM_ORDER.map((platform) => ({
      platform,
      sessions: ebkSessions.filter(
        (session) => session.platform === platform && session.shop_key === shop.shop_key,
      ),
    })),
  }));

  const unassignedSessions = ebkSessions.filter((session) => {
    const key = session.shop_key?.trim();
    return !key || !shopKeys.has(key);
  });

  return { competitorAccounts, shopNodes, unassignedSessions };
}
