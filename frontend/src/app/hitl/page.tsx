'use client';

import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, X, AlertTriangle, FileText, FileImage, FileVideo, Activity } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { api, HitlItem } from '@/lib/api';
import { useHitlWebSocket } from '@/lib/hitl-ws';

function modalityIcon(modality: string) {
  if (modality === 'pdf') return <FileText className="w-5 h-5 text-rose-400" />;
  if (modality === 'image' || modality === 'png' || modality === 'jpg')
    return <FileImage className="w-5 h-5 text-blue-400" />;
  if (modality === 'video' || modality === 'mp4')
    return <FileVideo className="w-5 h-5 text-violet-400" />;
  return <Activity className="w-5 h-5 text-amber-400" />;
}

function confidenceBadge(score: number): string {
  if (score >= 0.7) return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  if (score >= 0.5) return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
  return 'bg-rose-500/15 text-rose-400 border-rose-500/30';
}

function SkeletonCard() {
  return (
    <Card className="bg-black/40 border-white/10 backdrop-blur-2xl shadow-2xl w-full max-w-lg">
      <CardContent className="p-8 space-y-4">
        <div className="flex justify-between items-start">
          <div className="h-4 w-32 bg-white/10 rounded animate-pulse" />
          <div className="h-5 w-16 bg-white/10 rounded-full animate-pulse" />
        </div>
        <div className="h-4 w-48 bg-white/10 rounded animate-pulse" />
        <div className="h-24 bg-white/5 rounded-xl animate-pulse" />
        <div className="flex gap-4">
          <div className="flex-1 h-12 bg-white/5 rounded-xl animate-pulse" />
          <div className="flex-1 h-12 bg-white/5 rounded-xl animate-pulse" />
        </div>
      </CardContent>
    </Card>
  );
}

export default function HITLPage() {
  const [queue, setQueue] = useState<HitlItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const orgId = 'default';

  const loadQueue = useCallback(async () => {
    try {
      const data = await api.hitl.getQueue();
      setQueue(data.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadQueue();
  }, [loadQueue]);

  const handleWsMessage = useCallback(() => {
    void loadQueue();
  }, [loadQueue]);

  useHitlWebSocket(orgId, handleWsMessage);

  const handleDecision = async (itemId: string, decision: 'approve' | 'reject') => {
    setProcessingId(itemId);
    setError(null);
    try {
      if (decision === 'approve') {
        await api.hitl.approve(itemId);
      } else {
        await api.hitl.reject(itemId);
      }
      setQueue((prev) => prev.filter((i) => i.id !== itemId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Decision failed');
    } finally {
      setProcessingId(null);
    }
  };

  const currentItem = queue[0];

  return (
    <div className="p-8 pb-20 h-full flex flex-col">
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-1">
            Human-In-The-Loop
          </h1>
          <p className="text-zinc-400 text-sm">Review pipeline anomalies requiring human validation.</p>
        </div>
        {queue.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-rose-500/10 border border-rose-500/20 text-rose-400 text-sm">
            <AlertTriangle className="w-4 h-4" />
            {queue.length} pending
          </div>
        )}
      </div>

      {error && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="mb-6 px-4 py-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-sm flex items-center gap-2"
        >
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto">
            <X className="w-3.5 h-3.5" />
          </button>
        </motion.div>
      )}

      <div className="flex-1 flex items-center justify-center relative perspective-[1000px]">
        {loading ? (
          <SkeletonCard />
        ) : (
          <AnimatePresence mode="popLayout">
            {currentItem ? (
              <motion.div
                key={currentItem.id}
                initial={{ opacity: 0, scale: 0.85, rotateY: -15 }}
                animate={{ opacity: 1, scale: 1, rotateY: 0 }}
                exit={{ opacity: 0, scale: 1.1, filter: 'blur(8px)' }}
                transition={{ type: 'spring', bounce: 0.35, duration: 0.7 }}
                className="absolute w-full max-w-lg"
              >
                <Card className="bg-black/40 border-white/10 backdrop-blur-2xl shadow-2xl overflow-hidden">
                  <div className="absolute inset-0 bg-gradient-to-br from-rose-500/8 via-transparent to-transparent" />
                  <CardContent className="p-8 relative">
                    {/* Header */}
                    <div className="flex justify-between items-start mb-6">
                      <div className="flex items-center gap-2 text-rose-400">
                        <AlertTriangle className="w-5 h-5" />
                        <span className="font-semibold tracking-wide uppercase text-sm">
                          Anomaly Flagged
                        </span>
                      </div>
                      <span
                        className={`px-3 py-1 rounded-full border text-xs font-semibold ${confidenceBadge(currentItem.confidence)}`}
                      >
                        Score: {currentItem.confidence.toFixed(2)}
                      </span>
                    </div>

                    {/* Source */}
                    <div className="mb-6">
                      <h3 className="text-base text-white font-medium mb-1 flex items-center gap-2">
                        {modalityIcon(currentItem.finding.modality)}
                        Source: {currentItem.finding.modality.toUpperCase()}
                        {currentItem.finding.anomaly_type && (
                          <span className="ml-auto text-xs text-zinc-500 font-normal">
                            {currentItem.finding.anomaly_type}
                          </span>
                        )}
                      </h3>
                      <p className="text-zinc-300 leading-relaxed text-base bg-white/5 p-4 rounded-xl border border-white/5">
                        {currentItem.finding.description}
                      </p>
                    </div>

                    {/* Job reference */}
                    <p className="text-xs text-zinc-600 mb-6 font-mono">
                      Job: {currentItem.job_id.slice(0, 16)}…
                    </p>

                    {/* Action buttons */}
                    <div className="flex gap-4">
                      <button
                        onClick={() => void handleDecision(currentItem.id, 'reject')}
                        disabled={processingId === currentItem.id}
                        className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 border border-rose-500/20 transition-all font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {processingId === currentItem.id ? (
                          <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                        ) : (
                          <X className="w-5 h-5" />
                        )}
                        Reject Finding
                      </button>
                      <button
                        onClick={() => void handleDecision(currentItem.id, 'approve')}
                        disabled={processingId === currentItem.id}
                        className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/20 transition-all font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {processingId === currentItem.id ? (
                          <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                        ) : (
                          <Check className="w-5 h-5" />
                        )}
                        Validate & Approve
                      </button>
                    </div>

                    {/* Queue depth indicator */}
                    {queue.length > 1 && (
                      <p className="text-center text-xs text-zinc-600 mt-4">
                        {queue.length - 1} more item{queue.length - 1 !== 1 ? 's' : ''} in queue
                      </p>
                    )}
                  </CardContent>
                </Card>
              </motion.div>
            ) : (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="text-center text-zinc-500"
              >
                <div className="w-24 h-24 mx-auto mb-6 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
                  <Check className="w-12 h-12 text-emerald-500/60" />
                </div>
                <h2 className="text-xl font-medium text-white mb-2">Queue Empty</h2>
                <p>All anomalies have been resolved.</p>
              </motion.div>
            )}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}