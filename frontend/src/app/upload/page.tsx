'use client';

import { useState, useCallback, useRef, DragEvent, ChangeEvent } from 'react';
import {
  UploadCloud,
  CheckCircle,
  X,
  FileText,
  FileImage,
  FileVideo,
  FileBarChart2,
  File as FileIcon,
  ExternalLink,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Card, CardContent } from '@/components/ui/card';
import { api } from '@/lib/api';

const ACCEPTED = ['application/pdf', 'image/png', 'image/jpeg', 'video/mp4', 'text/csv'];
const ACCEPT_STR = '.pdf,.png,.jpg,.jpeg,.mp4,.csv';

function fileIcon(type: string) {
  if (type === 'application/pdf') return <FileText className="w-5 h-5 text-rose-400" />;
  if (type.startsWith('image/')) return <FileImage className="w-5 h-5 text-blue-400" />;
  if (type.startsWith('video/')) return <FileVideo className="w-5 h-5 text-violet-400" />;
  if (type === 'text/csv') return <FileBarChart2 className="w-5 h-5 text-emerald-400" />;
  return <FileIcon className="w-5 h-5 text-zinc-400" />;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export default function UploadPage() {
  const [isDragging, setIsDragging] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const valid = Array.from(incoming).filter(
      (f) => ACCEPTED.includes(f.type) || f.name.endsWith('.csv'),
    );
    if (valid.length < Array.from(incoming).length) {
      setError('Some files were skipped — only PDF, PNG, JPG, MP4, CSV are supported.');
    }
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...valid.filter((f) => !names.has(f.name))];
    });
  };

  const handleDrag = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setIsDragging(true);
    else if (e.type === 'dragleave') setIsDragging(false);
  };

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    addFiles(e.dataTransfer.files);
  }, []);

  const handleInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    addFiles(e.target.files);
    e.target.value = '';
  };

  const removeFile = (name: string) => {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const handleUpload = async () => {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    setProgress(0);
    try {
      const result = await api.ingest.upload(files, setProgress);
      setJobId(result.job_id);
      setFiles([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const resetAll = () => {
    setJobId(null);
    setError(null);
    setProgress(0);
    setFiles([]);
  };

  return (
    <div className="p-8 pb-20 h-full flex flex-col">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-white mb-1">Ingest Hub</h1>
        <p className="text-zinc-400 text-sm">
          Stream multi-modal files (PDF, Video, Sensor Data) directly to MinIO.
        </p>
      </div>

      <div className="flex-1 flex flex-col items-center justify-start gap-6 max-w-2xl mx-auto w-full">
        {/* Success state */}
        <AnimatePresence>
          {jobId && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full"
            >
              <Card className="bg-black/40 border-emerald-500/30 backdrop-blur-md">
                <CardContent className="p-8 flex flex-col items-center text-center">
                  <div className="w-20 h-20 bg-emerald-500/20 rounded-full flex items-center justify-center mb-6 border border-emerald-500/50">
                    <CheckCircle className="w-10 h-10 text-emerald-400" />
                  </div>
                  <h3 className="text-xl font-medium text-emerald-400 mb-2">Ingestion Complete</h3>
                  <p className="text-zinc-400 mb-4">Pipeline triggered on RabbitMQ queue.</p>
                  <div className="flex items-center gap-2 px-4 py-2 bg-white/5 rounded-lg border border-white/10 text-sm font-mono text-zinc-300 mb-6">
                    <span className="text-zinc-500">Job ID:</span>
                    <span className="text-white">{jobId}</span>
                    <a
                      href={`/jobs/${jobId}`}
                      className="ml-2 text-emerald-400 hover:text-emerald-300"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  </div>
                  <button
                    onClick={resetAll}
                    className="px-6 py-2.5 bg-white/10 hover:bg-white/15 border border-white/10 rounded-xl text-white text-sm transition-all"
                  >
                    Upload More Files
                  </button>
                </CardContent>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>

        {!jobId && (
          <>
            {/* Drop zone */}
            <motion.div animate={{ scale: isDragging ? 1.02 : 1 }} className="w-full">
              <Card
                className={`bg-black/40 border-2 backdrop-blur-md transition-colors duration-300 cursor-pointer ${
                  isDragging
                    ? 'border-emerald-500 bg-emerald-500/5'
                    : 'border-white/10 border-dashed hover:border-white/20'
                }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
              >
                <CardContent className="p-12 flex flex-col items-center justify-center text-center">
                  <div className="w-16 h-16 bg-white/5 rounded-full flex items-center justify-center mb-5 border border-white/10">
                    <UploadCloud className="w-8 h-8 text-zinc-400" />
                  </div>
                  <h3 className="text-lg font-medium text-white mb-2">
                    Drag & Drop Intelligence
                  </h3>
                  <p className="text-zinc-400 mb-1 max-w-sm text-sm">
                    Drop files here or click to browse. Triggers the LangGraph analysis pipeline.
                  </p>
                  <p className="text-zinc-600 text-xs">PDF · PNG · JPG · MP4 · CSV</p>
                </CardContent>
              </Card>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPT_STR}
                onChange={handleInputChange}
                className="hidden"
              />
            </motion.div>

            {/* Error */}
            {error && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="w-full px-4 py-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-sm flex items-center gap-2"
              >
                <X className="w-4 h-4 shrink-0" />
                {error}
                <button onClick={() => setError(null)} className="ml-auto">
                  <X className="w-3.5 h-3.5" />
                </button>
              </motion.div>
            )}

            {/* File list */}
            <AnimatePresence>
              {files.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="w-full"
                >
                  <Card className="bg-black/40 border-white/10 backdrop-blur-md overflow-hidden">
                    {/* Progress bar */}
                    {uploading && (
                      <motion.div
                        className="h-0.5 bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.6)]"
                        initial={{ width: '0%' }}
                        animate={{ width: `${progress}%` }}
                        transition={{ ease: 'linear' }}
                      />
                    )}
                    <CardContent className="p-4 space-y-2">
                      <div className="flex items-center justify-between mb-3">
                        <p className="text-sm font-medium text-zinc-300">
                          {files.length} file{files.length !== 1 ? 's' : ''} selected
                        </p>
                        <button
                          onClick={() => setFiles([])}
                          className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
                        >
                          Clear all
                        </button>
                      </div>
                      {files.map((file) => (
                        <div
                          key={file.name}
                          className="flex items-center gap-3 px-3 py-2.5 bg-white/5 rounded-lg border border-white/5"
                        >
                          {fileIcon(file.type)}
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-white truncate">{file.name}</p>
                            <p className="text-xs text-zinc-500">{formatBytes(file.size)}</p>
                          </div>
                          {!uploading && (
                            <button
                              onClick={() => removeFile(file.name)}
                              className="text-zinc-600 hover:text-zinc-300 transition-colors"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      ))}

                      <button
                        onClick={handleUpload}
                        disabled={uploading || files.length === 0}
                        className="w-full mt-2 py-3 rounded-xl bg-emerald-500 hover:bg-emerald-400 disabled:bg-emerald-500/40 disabled:cursor-not-allowed text-black font-semibold text-sm transition-all duration-200 flex items-center justify-center gap-2"
                      >
                        {uploading ? (
                          <>
                            <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                              />
                            </svg>
                            Uploading… {progress}%
                          </>
                        ) : (
                          <>
                            <UploadCloud className="w-4 h-4" />
                            Upload {files.length} file{files.length !== 1 ? 's' : ''}
                          </>
                        )}
                      </button>
                    </CardContent>
                  </Card>
                </motion.div>
              )}
            </AnimatePresence>
          </>
        )}
      </div>
    </div>
  );
}