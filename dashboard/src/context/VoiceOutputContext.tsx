import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useVoiceOutput } from "../hooks/useVoiceOutput";

type VoiceOutputValue = ReturnType<typeof useVoiceOutput>;

const VoiceOutputContext = createContext<VoiceOutputValue | null>(null);

export function VoiceOutputProvider({ children }: { children: ReactNode }) {
  const { speakingId, speak, stop } = useVoiceOutput();
  const value = useMemo(
    () => ({ speakingId, speak, stop }),
    [speakingId, speak, stop],
  );
  return (
    <VoiceOutputContext.Provider value={value}>
      {children}
    </VoiceOutputContext.Provider>
  );
}

export function useVoiceOutputContext(): VoiceOutputValue {
  const ctx = useContext(VoiceOutputContext);
  if (!ctx) {
    throw new Error(
      "useVoiceOutputContext must be used within VoiceOutputProvider",
    );
  }
  return ctx;
}
