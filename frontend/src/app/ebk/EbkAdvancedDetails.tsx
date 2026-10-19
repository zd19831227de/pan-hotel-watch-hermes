import type { ReactNode } from "react";

export function EbkAdvancedDetails(props: {
  title?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="ebk-advanced-details" open={props.defaultOpen ? true : undefined}>
      <summary>{props.title ?? "高级配置与技术详情"}</summary>
      <div className="ebk-advanced-details-body">{props.children}</div>
    </details>
  );
}
