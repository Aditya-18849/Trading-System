'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';

interface KillSwitchProps {
  onToggle?: (enabled: boolean) => void;
}

export function KillSwitch({ onToggle }: KillSwitchProps) {
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Fetch current kill switch state from backend
    api.get('/api/system/kill-switch').then(res => {
      setEnabled(res.data.enabled);
    }).catch(() => {
      // Default to enabled
      setEnabled(true);
    });
  }, []);

  const handleToggle = async () => {
    setLoading(true);
    try {
      await api.post('/api/system/kill-switch', { enabled: !enabled });
      setEnabled(!enabled);
      onToggle?.(!enabled);
    } catch (err) {
      console.error('Failed to toggle kill switch:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleToggle}
      disabled={loading}
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-colors ${
        enabled
          ? 'bg-primary-50 border-primary-200 text-primary-700 hover:bg-primary-100 dark:bg-primary-900/20 dark:border-primary-800 dark:text-primary-400'
          : 'bg-danger-50 border-danger-200 text-danger-700 hover:bg-danger-100 dark:bg-danger-900/20 dark:border-danger-800 dark:text-danger-400'
      }`}
      title={enabled ? 'Click to pause auto-execution' : 'Click to resume auto-execution'}
    >
      <span className={`w-2 h-2 rounded-full ${enabled ? 'bg-primary-500' : 'bg-danger-500'}`}></span>
      <span className="text-sm font-medium">
        {enabled ? 'AUTO ON' : 'AUTO PAUSED'}
      </span>
      {loading && <span className="animate-spin text-xs">⟳</span>}
    </button>
  );
}