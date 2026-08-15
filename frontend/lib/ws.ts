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
  private messageHandlers: Set<WSMessageHandler> = new Set();

  constructor(url: string, options: WSOptions = {}) {
    this.url = url;
    this.options = {
      onOpen: options.onOpen ?? (() => {}),
      onClose: options.onClose ?? (() => {}),
      onError: options.onError ?? (() => {}),
      onMessage: options.onMessage ?? (() => {}),
      reconnect: options.reconnect ?? true,
      reconnectInterval: options.reconnectInterval ?? 3000,
      maxReconnectAttempts: options.maxReconnectAttempts ?? 10,
    };
  }

  connect(path: string, onMessage?: WSMessageHandler) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.close();
    }

    if (onMessage) {
      this.messageHandlers.add(onMessage);
    }

    const wsUrl = `${this.url.replace(/^http/, 'ws')}${path}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
      this.options.onOpen();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.messageHandlers.forEach(handler => handler(data));
        this.options.onMessage(data);
      } catch (err) {
        console.error('Failed to parse WS message:', err);
      }
    };

    this.ws.onclose = () => {
      console.log('WebSocket disconnected');
      this.options.onClose();
      this.attemptReconnect(path);
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      this.options.onError(error);
    };
  }

  private attemptReconnect(path: string) {
    if (!this.options.reconnect) return;
    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      console.log('Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      console.log(`Reconnecting... (attempt ${this.reconnectAttempts})`);
      this.connect(path);
    }, this.options.reconnectInterval);
  }

  disconnect() {
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
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  onMessage(handler: WSMessageHandler) {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  get readyState() {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }
}

// Singleton instance
const wsBaseUrl = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';
export const ws = new WSClient(wsBaseUrl, {
  reconnect: true,
  reconnectInterval: 3000,
  maxReconnectAttempts: 10,
});

export function useWebSocket(path: string, onMessage: WSMessageHandler) {
  if (typeof window === 'undefined') return;

  ws.connect(path, onMessage);

  return () => {
    // Don't disconnect globally, just remove handler
  };
}