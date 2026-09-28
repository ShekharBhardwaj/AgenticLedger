import { useEffect, useState } from "react";
import { apiKey, whoami } from "./api";

/** The projects the signed-in person is scoped to (0.16), or null when
 *  unscoped or unknown. Read once per view; a scope only changes at
 *  sign-in. */
export function useScope(): string[] | null {
  const [scope, setScope] = useState<string[] | null>(null);
  useEffect(() => {
    whoami(apiKey).then((w) => setScope(w.projects && w.projects.length ? w.projects : null))
      .catch(() => setScope(null));
  }, []);
  return scope;
}
