import axios from 'axios';
import { getToken, removeToken } from './auth';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
});

// Attach JWT token automatically
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: handle 401 Unauthorized & 402 Payment Required
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (typeof window !== 'undefined') {
      // 401: Expired or invalid token
      if (error.response?.status === 401) {
        const currentPath = window.location.pathname;
        if (currentPath !== '/login') {
          removeToken();
          window.location.href = '/login';
        }
      }

      // 402: Vendor Subscription Kill-Switch Active
      if (error.response?.status === 402) {
        window.dispatchEvent(
          new CustomEvent('license_suspended', {
            detail: error.response.data?.detail || 'Subscription payment required.',
          })
        );
      }
    }
    return Promise.reject(error);
  }
);