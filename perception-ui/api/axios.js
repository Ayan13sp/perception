import axios from 'axios';
import { useAuthStore } from '@/store/auth';

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
});

api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().user?.access_token;
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// An expired or invalid session logs the user out instead of leaving a broken dashboard
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url || '';
    const isAuthCall = url.startsWith('/auth/login') || url.startsWith('/auth/signup');
    if (error.response?.status === 401 && !isAuthCall) {
      useAuthStore.getState().logout();
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export default api;
