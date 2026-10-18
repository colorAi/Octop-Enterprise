import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  useCurrentUser,
  useSetCurrentUser,
} from "../../../hooks/useCurrentUser";
import { userCan } from "../../../utils/permissions";
import { authApi } from "../../../api/modules/auth";
import {
  AUTH_TOKEN_CHANGED_EVENT,
  UNAUTHORIZED_EVENT,
} from "../../../api/request";
import {
  CommunicationClient,
  authSessionIdentity,
} from "./communicationClient";

const Context = createContext<CommunicationClient | null>(null);
export function CommunicationProvider({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  const setUser = useSetCurrentUser();
  const [identity, setIdentity] = useState(authSessionIdentity);
  const messaging = userCan(user, "messaging"),
    contacts = userCan(user, "contacts");
  const account = user?.id;
  const client = useMemo(
    () =>
      new CommunicationClient(
        account && identity.startsWith(`${account}:`) ? identity : "",
        contacts,
        messaging,
        () => {
          void authApi
            .me()
            .then((fresh) => {
              if (authSessionIdentity() === identity && fresh.id === account)
                setUser(fresh);
            })
            .catch(() => undefined);
        },
      ),
    [identity, contacts, messaging, setUser, account],
  );
  useEffect(() => {
    const changed = () => {
      if (authSessionIdentity() !== identity) client.stop();
      setIdentity(authSessionIdentity());
    };
    const denied = () => client.stop();
    window.addEventListener(AUTH_TOKEN_CHANGED_EVENT, changed);
    window.addEventListener("storage", changed);
    window.addEventListener(UNAUTHORIZED_EVENT, denied);
    let active = true;
    queueMicrotask(() => {
      if (
        active &&
        account &&
        identity.startsWith(`${account}:`) &&
        (messaging || contacts)
      )
        client.start();
    });
    return () => {
      active = false;
      client.stop();
      window.removeEventListener(AUTH_TOKEN_CHANGED_EVENT, changed);
      window.removeEventListener("storage", changed);
      window.removeEventListener(UNAUTHORIZED_EVENT, denied);
    };
  }, [client, identity, account, messaging, contacts]);
  return <Context.Provider value={client}>{children}</Context.Provider>;
}
export function useCommunication() {
  const client = useContext(Context);
  if (!client) throw new Error("CommunicationProvider required");
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  return { client, state };
}
