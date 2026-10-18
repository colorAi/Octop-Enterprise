import { createContext, useContext, type ReactNode } from "react";
import ServiceRestartOverlay from "../components/ServiceRestartOverlay";
import {
  useServiceRestart,
  type RestartPhase,
} from "../hooks/useServiceRestart";

interface ServiceRestartContextValue {
  restartPhase: RestartPhase;
  isRestarting: boolean;
  requestRestart: () => void;
  executeRestart: () => Promise<void>;
  resetRestart: () => void;
}

const ServiceRestartContext = createContext<ServiceRestartContextValue | null>(
  null,
);

export function ServiceRestartProvider({ children }: { children: ReactNode }) {
  const restart = useServiceRestart();

  return (
    <ServiceRestartContext.Provider value={restart}>
      {children}
      <ServiceRestartOverlay
        phase={restart.restartPhase}
        onConfirm={() => void restart.executeRestart()}
        onCancel={restart.resetRestart}
        onDismiss={restart.resetRestart}
        onRetry={() => void restart.executeRestart()}
      />
    </ServiceRestartContext.Provider>
  );
}

export function useServiceRestartContext(): ServiceRestartContextValue {
  const ctx = useContext(ServiceRestartContext);
  if (!ctx) {
    throw new Error(
      "useServiceRestartContext must be used within ServiceRestartProvider",
    );
  }
  return ctx;
}
