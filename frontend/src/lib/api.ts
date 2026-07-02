const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function getAuthHeader(): HeadersInit {
  const token = typeof window !== 'undefined' ? localStorage.getItem('aether_token') : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader(),
      ...options?.headers,
    },
  });
  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('aether_token');
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'API error');
  }
  return res.json() as Promise<T>;
}

export const api = {
  auth: {
    login: (email: string, password: string) =>
      apiFetch<{ access_token: string; token_type: string }>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
  },
  jobs: {
    get: (jobId: string) => apiFetch<Job>(`/api/v1/jobs/${jobId}`),
    getReport: (jobId: string) => apiFetch<Report>(`/api/v1/jobs/${jobId}/report`),
  },
  ingest: {
    upload: (files: File[], onProgress?: (pct: number) => void) =>
      uploadFiles(files, onProgress),
  },
  hitl: {
    getQueue: () => apiFetch<{ items: HitlItem[] }>('/api/v1/hitl/queue'),
    approve: (itemId: string) =>
      apiFetch(`/api/v1/hitl/${itemId}/approve`, { method: 'POST' }),
    reject: (itemId: string) =>
      apiFetch(`/api/v1/hitl/${itemId}/reject`, { method: 'POST' }),
  },
  audit: {
    getEvents: (limit = 50) =>
      apiFetch<{ events: AuditEvent[] }>(`/api/v1/audit/events?limit=${limit}`),
  },
};

async function uploadFiles(
  files: File[],
  onProgress?: (pct: number) => void,
): Promise<{ job_id: string }> {
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/api/v1/ingest/upload`);
    const token =
      typeof window !== 'undefined' ? localStorage.getItem('aether_token') : null;
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as { job_id: string });
      } else {
        reject(new Error(xhr.statusText || 'Upload failed'));
      }
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(formData);
  });
}

// Types
export interface Job {
  job_id: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  created_at: string;
  org_id: string;
}

export interface Report {
  job_id: string;
  report_id: string;
  summary: string;
  content: Record<string, unknown>;
  version: number;
  created_at: string;
}

export interface HitlItem {
  id: string;
  job_id: string;
  finding: {
    description: string;
    modality: string;
    anomaly_type?: string;
  };
  confidence: number;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}