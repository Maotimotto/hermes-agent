/**
 * ErrorToast — 右下角浮动错误提示，带 motion enter/exit。
 */

import { motion } from "motion/react";

export function ErrorToast({ message }: { message: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 16, scale: 0.95 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      style={{
        position: "fixed",
        bottom: 16,
        right: 16,
        background: "#fef2f2",
        border: "1px solid #fca5a5",
        color: "#991b1b",
        padding: "8px 12px",
        borderRadius: 6,
        fontSize: 13,
        maxWidth: 400,
        boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
      }}
    >
      {message}
    </motion.div>
  );
}
