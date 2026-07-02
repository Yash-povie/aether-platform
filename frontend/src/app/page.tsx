'use client';

import { useEffect, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Activity, ShieldAlert, CheckCircle2, Clock, RefreshCw } from 'lucide-react';
import { motion } from 'framer-motion';
import { api, AuditEvent } from '@/lib/api';

interface Stats {
  total: number;
  completed: number;
  processing: number;
  failed: number;
}

function SkeletonCard() {
  return (
    <Card className="bg-black/40 border-white/10 backdrop-blur-md overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="h-3 w-32 bg-white/10 rounded animate-pulse" />
        <div className="h-4 w-4 bg-white/10 rounded animate-pulse" />
      </CardHeader>
      <CardContent>
        <div className="h-7 w-20 bg-white/10 rounded animate-pulse" />
      </CardContent>
    </Card>
  );
}

function SkeletonRow() {
  return (
    <tr className="border-t border-white/5">
      <td className="py-3 px-4"><div className="h-3 w-28 bg-white/10 rounded animate-pulse" /></td>
      <td className="py-3 px-4"><div className="h-3 w-40 bg-white/10 rounded animate-pulse" /></td>
      <td className="py-3 px-4"><div className="h-3 w-20 bg-white/10 rounded animate-pulse" /></td>
      <td className="py-3 px-4"><div className="h-3 w-24 bg-white/10 rounded animate-pulse" /></td>
    </tr>
  );
}

function eventTypeColor(type: string): string {
  if (type.includes('error') || type.includes('fail')) return 'text-rose-400 bg-rose-500/10 border-rose-500/20';
  if (type.includes('approve') || type.includes('complete') || type.includes('success')) return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';
  if (type.includes('upload') || type.includes('ingest') || type.includes('start')) return 'text-blue-400 bg-blue-500/10 border-blue-500/20';
  return 'text-zinc-400 bg-white/5 border-white/10';
}

function formatRelative(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(dateStr).toLocaleDateString();
}

export default function Dashboard() {
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [stats, setStats] = useState<Stats>({ total: 0, completed: 0, processing: 0, failed: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadDashboard = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const eventsData = await api.audit.getEvents(20);
      const events = eventsData.events ?? [];
      setAuditEvents(events);

      // Derive stats from audit events
      const total = events.length;
      const completed = events.filter((e) =>
        e.event_type.includes('complete') || e.event_type.includes('approve'),
      ).length;
      const processing = events.filter((e) =>
        e.event_type.includes('start') || e.event_type.includes('processing'),
      ).length;
      const failed = events.filter((e) =>
        e.event_type.includes('fail') || e.event_type.includes('error'),
      ).length;
      setStats({ total, completed, processing, failed });
      setLastRefresh(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
    const interval = setInterval(() => void loadDashboard(true), 30000);
    return () => clearInterval(interval);
  }, [loadDashboard]);

  const statCards = [
    { title: 'Total Events', value: loading ? '—' : String(stats.total), icon: Activity, color: 'text-blue-400' },
    { title: 'Completed / Approved', value: loading ? '—' : String(stats.completed), icon: CheckCircle2, color: 'text-emerald-400' },
    { title: 'In Progress', value: loading ? '—' : String(stats.processing), icon: Clock, color: 'text-violet-400' },
    { title: 'Errors / Failures', value: loading ? '—' : String(stats.failed), icon: ShieldAlert, color: 'text-rose-400' },
  ];

  return (
    <div className="p-8 pb-20">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-1">Overview</h1>
          <p className="text-zinc-400 text-sm">System intelligence and anomaly detection metrics.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-zinc-500">
              Updated {formatRelative(lastRefresh.toISOString())}
            </span>
          )}
          <button
            onClick={() => void loadDashboard(true)}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-zinc-400 hover:text-white text-sm transition-all disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6 px-4 py-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-sm flex items-center gap-2"
        >
          <ShieldAlert className="w-4 h-4 shrink-0" />
          {error}
          <button
            onClick={() => void loadDashboard()}
            className="ml-auto underline underline-offset-2 hover:no-underline"
          >
            Retry
          </button>
        </motion.div>
      )}

      {/* Stat Cards */}
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4 mb-8">
        {loading
          ? Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)
          : statCards.map((stat, i) => (
              <motion.div
                key={stat.title}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.08 }}
              >
                <Card className="bg-black/40 border-white/10 backdrop-blur-md overflow-hidden relative group">
                  <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-sm font-medium text-zinc-400">{stat.title}</CardTitle>
                    <stat.icon className={`h-4 w-4 ${stat.color}`} />
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold text-white">{stat.value}</div>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
      </div>

      {/* Audit Events Table */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
      >
        <Card className="bg-black/40 border-white/10 backdrop-blur-md overflow-hidden">
          <CardContent className="p-0">
            <div className="px-6 py-4 border-b border-white/5 flex items-center justify-between">
              <h3 className="text-base font-semibold text-white">Recent Audit Events</h3>
              <span className="text-xs text-zinc-500">Live · refreshes every 30s</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-zinc-500 text-xs uppercase tracking-wider">
                    <th className="py-3 px-4 font-medium">Event Type</th>
                    <th className="py-3 px-4 font-medium">Entity</th>
                    <th className="py-3 px-4 font-medium">Entity ID</th>
                    <th className="py-3 px-4 font-medium">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {loading
                    ? Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} />)
                    : auditEvents.length === 0
                    ? (
                      <tr>
                        <td colSpan={4} className="py-12 text-center text-zinc-500">
                          No audit events found
                        </td>
                      </tr>
                    )
                    : auditEvents.map((event) => (
                        <motion.tr
                          key={event.id}
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          className="border-t border-white/5 hover:bg-white/3 transition-colors"
                        >
                          <td className="py-3 px-4">
                            <span
                              className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium border ${eventTypeColor(event.event_type)}`}
                            >
                              {event.event_type}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-zinc-300">{event.entity_type}</td>
                          <td className="py-3 px-4 text-zinc-500 font-mono text-xs">
                            {event.entity_id.length > 20
                              ? `${event.entity_id.slice(0, 8)}…${event.entity_id.slice(-6)}`
                              : event.entity_id}
                          </td>
                          <td className="py-3 px-4 text-zinc-500">
                            {formatRelative(event.created_at)}
                          </td>
                        </motion.tr>
                      ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}