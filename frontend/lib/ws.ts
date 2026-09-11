import { useTradingStore } from './store';

type WSMessageHandler = (data: any) => void;

interface WSOptions {
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
  onMessage?: WSMessageHandler;
  reconnect?: boolean;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

class WSClient {
  private ws: WebSocket | null = null;
  private url: string;
  private options: Required<WSOptions>;
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pingTimer: NodeJS.Timeout | null = null;
  private messageHandlers: Set<WSMessageHandler> = new Set();
  private pingStartTime = 0;

  constructor(url: string, options: WSOptions = {}) {
    this.url = url;
    this.options = {
      onOpen: options.onOpen ?? (() => {}),
      onClose: options.onClose ?? (() => {}),
      onError: options.onError ?? (() => {}),
      onMessage: options.onMessage ?? (() => {}),
      reconnect: options.reconnect ?? true,
      reconnectInterval: options.reconnectInterval ?? 2000,
      maxReconnectAttempts: options.maxReconnectAttempts ?? 25,
    };
  }

  connect(path: string = '/ws/live', onMessage?: WSMessageHandler) {
    if (typeof window === 'undefined') return;

    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      if (onMessage) this.messageHandlers.add(onMessage);
      return;
    }

    if (onMessage) {
      this.messageHandlers.add(onMessage);
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = process.env.NEXT_PUBLIC_API_URL 
      ? process.env.NEXT_PUBLIC_API_URL.replace(/^http(s)?:\/\//, '')
      : '127.0.0.1:8000';

    const wsUrl = `${protocol}//${host}${path}`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        useTradingStore.getState().setWsConnected(true);
        this.options.onOpen();

        // Start ping keepalive
        this.startPingKeepalive();
      };

      this.ws.onmessage = (event) => {
        if (event.data === 'pong') {
          const latency = Math.max(5, Date.now() - this.pingStartTime);
          useTradingStore.getState().setWsLatency(latency);
          return;
        }

        try {
          const data = JSON.parse(event.data);

          // Route directly to global Zustand store
          if (data.type === 'tick_update') {
            useTradingStore.getState().updateTick(data.payload);
          } else if (data.type === 'portfolio_update') {
            useTradingStore.getState().updatePortfolio(data.payload);
          }

          this.messageHandlers.forEach((handler) => handler(data));
          this.options.onMessage(data);
        } catch (err) {
          console.debug('Received WS message:', event.data);
        }
      };

      this.ws.onclose = () => {
        useTradingStore.getState().setWsConnected(false);
        this.stopPingKeepalive();
        this.options.onClose();
        this.attemptReconnect(path);
      };

      this.ws.onerror = (error) => {
        useTradingStore.getState().setWsConnected(false);
        this.options.onError(error);
      };
    } catch (e) {
      console.error('Failed to initiate WebSocket:', e);
    }
  }

  private startPingKeepalive() {
    this.stopPingKeepalive();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.pingStartTime = Date.now();
        this.ws.send('ping');
      }
    }, 8000);
  }

  private stopPingKeepalive() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private attemptReconnect(path: string) {
    if (!this.options.reconnect) return;
    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      return;
    }

    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.connect(path);
    }, this.options.reconnectInterval);
  }

  disconnect() {
    this.stopPingKeepalive();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.options.reconnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  send(data: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }
}

// Global WebSocket Singleton instance
export const ws = new WSClient('http://127.0.0.1:8000');