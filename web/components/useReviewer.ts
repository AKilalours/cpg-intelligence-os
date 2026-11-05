"use client";

import { useEffect, useState } from "react";

// The reviewer's name, remembered in this browser only. Read after mount so the server and
// client render the same HTML.
export function useReviewer(): [string, (v: string) => void] {
  const [name, setName] = useState("");
  useEffect(() => {
    try { setName(localStorage.getItem("reviewer") ?? ""); } catch { /* private window */ }
  }, []);
  const save = (v: string) => {
    setName(v);
    try { localStorage.setItem("reviewer", v); } catch { /* private window */ }
  };
  return [name, save];
}
