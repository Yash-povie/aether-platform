'use client';
import { useEffect, useRef, useCallback } from 'react';

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';

export function useHitlWebSocket(
  orgId: string,
  onMessage: (data: unknown) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const onMessageRef = useRef(onMessage);

  // Keep onMessage ref up to date without restarting the socket
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  const connect = useCallback(() => {
    const token =
      typeof window !== 'undefined' ? localStorage.getItem('aether_token') : null;
    const url = `${WS_BASE}/api/v1/hitl/ws/${orgId}${token ? `?token=${token}` : ''}`;
    const ws = new WebSocket(url);

    ws.onopen = () => console.log('[HITL WS] Connected');
    ws.onmessage = (e) => {
      try {
        onMessageRef.current(JSON.parse(e.data as string));
      } catch {
        onMessageRef.current(e.data);
      }
    };
    ws.onclose = (e) => {
      console.log('[HITL WS] Disconnected, reconnecting in 3s…', e.code);
      reconnectTimer.current = setTimeout(connect, 3000);
    };
    ws.onerror = (e) => console.error('[HITL WS] Error:', e);
    wsRef.current = ws;
  }, [orgId]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);
}