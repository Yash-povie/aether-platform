"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Upload, Users, Settings, Activity } from "lucide-react";
import { cn } from "@/lib/utils";

const navigation = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Ingest Hub", href: "/upload", icon: Upload },
  { name: "HITL Review", href: "/hitl", icon: Users },
  { name: "System Metrics", href: "/metrics", icon: Activity },
  { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <div className="flex h-screen w-64 flex-col border-r border-white/5 bg-black/40 backdrop-blur-xl">
      <div className="flex h-16 shrink-0 items-center px-6">
        <h1 className="text-xl font-semibold tracking-tight text-white flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-emerald-500/20 border border-emerald-500/50 flex items-center justify-center">
            <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          </div>
          AETHER
        </h1>
      </div>
      <nav className="flex flex-1 flex-col px-4 py-4 gap-1">
        {navigation.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                "group flex items-center gap-x-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200",
                isActive
                  ? "bg-white/10 text-white border border-white/10 shadow-[0_0_15px_rgba(255,255,255,0.05)]"
                  : "text-zinc-400 hover:bg-white/5 hover:text-white"
              )}
            >
              <item.icon
                className={cn(
                  "h-5 w-5 shrink-0 transition-colors duration-200",
                  isActive ? "text-emerald-400" : "text-zinc-500 group-hover:text-zinc-300"
                )}
                aria-hidden="true"
              />
              {item.name}
            </Link>
          );
        })}
      </nav>
      <div className="p-4">
        <div className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-md">
          <p className="text-xs font-semibold text-zinc-300 mb-1">System Status</p>
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
            All nodes operational
          </div>
        </div>
      </div>
    </div>
  );
}
