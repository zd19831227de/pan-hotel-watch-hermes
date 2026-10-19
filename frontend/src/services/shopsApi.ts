import { apiRequest } from "./http";

export type Shop = {
  shop_key: string;
  name: string;
  short_name?: string | null;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
};

export type ShopCreateInput = {
  name: string;
  short_name?: string | null;
  sort_order?: number;
};

export async function listShops(): Promise<Shop[]> {
  const payload = await apiRequest<{ shops: Shop[] }>("/api/v1/shops");
  return payload.shops;
}

export async function createShop(input: ShopCreateInput): Promise<Shop> {
  const payload = await apiRequest<{ shop: Shop }>("/api/v1/shops", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return payload.shop;
}
