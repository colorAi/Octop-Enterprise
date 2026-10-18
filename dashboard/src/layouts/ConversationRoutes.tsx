import { Suspense, useRef, type ReactNode } from "react";
import { Routes, useLocation, type Location } from "react-router-dom";
import PageLoading from "../components/PageLoading";
import { ConversationPageVisibility } from "../hooks/useConversationPageVisible";
import { isChatPath } from "./chatHistoryRail";

/** Keep Agent and company pages mounted while switching between their nav entries. */
export default function ConversationRoutes({
  children,
}: {
  children: ReactNode;
}) {
  const location = useLocation();
  const active = isChatPath(location.pathname) ? "agent" : "company";
  const locations = useRef<Partial<Record<"agent" | "company", Location>>>({});
  locations.current[active] = location;

  return (
    <div
      style={{
        position: "relative",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      {(["agent", "company"] as const).map((tab) => {
        const saved = locations.current[tab];
        if (!saved) return null;
        const visible = active === tab;
        return (
          <div
            key={tab}
            aria-hidden={!visible}
            {...(!visible ? { inert: "" } : {})}
            style={{
              position: "absolute",
              inset: 0,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              // display:none would restart entrance animations and zero list sizes.
              visibility: visible ? "visible" : "hidden",
              // Descendants (e.g. the turn rail) can override inherited visibility.
              // Composite the whole inactive page away without resetting its layout.
              opacity: visible ? 1 : 0,
              pointerEvents: visible ? "auto" : "none",
            }}
          >
            <ConversationPageVisibility.Provider value={visible}>
              <Suspense fallback={<PageLoading />}>
                {/* Freeze params, search and router state for the hidden tab. */}
                <Routes location={saved}>{children}</Routes>
              </Suspense>
            </ConversationPageVisibility.Provider>
          </div>
        );
      })}
    </div>
  );
}
