/**
 * useApprovals — pending approvals list with auto-refresh.
 *
 * Polls GET /control-plane/approvals?status=pending every 5s and exposes a
 * `decide` helper. The list endpoint is best-effort: missing/erroring routes
 * surface as an empty list instead of an error toast.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";
import type {
  ApprovalDecision,
  ApprovalRecord,
  ApprovalsResponse,
} from "@/pages/control-plane/types";

const REFRESH_MS = 5000;

export type UseApprovalsResult = {
  approvals: ApprovalRecord[];
  error: string | null;
  refresh: () => Promise<void>;
  decide: (
    approvalId: string,
    decision: Exclude<ApprovalDecision, "pending">,
  ) => Promise<void>;
};

export function useApprovals(): UseApprovalsResult {
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchJSON<ApprovalsResponse>(
        "/control-plane/approvals?status=pending",
      );
      setApprovals(data);
    } catch {
      // Endpoint may not be ready in older builds — render empty state silently.
      setApprovals([]);
    }
  }, []);

  const decide = useCallback(
    async (
      approvalId: string,
      decision: Exclude<ApprovalDecision, "pending">,
    ) => {
      try {
        await fetchJSON(
          `/control-plane/approvals/${approvalId}/decision`,
          {
            method: "POST",
            body: JSON.stringify({ decision, decided_by: "user" }),
            headers: { "Content-Type": "application/json" },
          },
        );
        await refresh();
      } catch (e) {
        setError(`Approval decision failed: ${(e as Error).message}`);
      }
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
    const t = setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  return { approvals, error, refresh, decide };
}
