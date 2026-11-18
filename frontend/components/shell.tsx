"use client";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

const links = [["Overview","/"],["Upload","/upload"],["Jobs","/jobs"],["Speaking feedback","/speaking"],["Templates","/templates"],["Grading rules","/grading-rules"],["Batch mapping","/mapping"]];
export function Shell({ children }: { children: ReactNode }) {
  const [dark,setDark]=useState(false);
  useEffect(()=>{document.documentElement.classList.toggle("dark",dark)},[dark]);
  return <div className="min-h-screen md:flex"><aside className="border-b border-slate-200 p-5 dark:border-slate-800 md:min-h-screen md:w-60 md:border-b-0 md:border-r"><Link className="text-xl font-bold tracking-tight" href="/">◉ Transcripto</Link><nav className="mt-8 flex gap-3 md:flex-col">{links.map(([n,h])=><Link className="navlink" key={h} href={h}>{n}</Link>)}</nav><button className="navlink mt-6" onClick={()=>setDark(!dark)}>Toggle {dark?"light":"dark"} mode</button></aside><main className="mx-auto w-full max-w-6xl p-5 md:p-10">{children}</main></div>
}
export function PageTitle({title,subtitle,action}:{title:string;subtitle:string;action?:ReactNode}){return <header className="mb-8 flex items-start justify-between gap-4"><div><h1 className="text-3xl font-semibold tracking-tight">{title}</h1><p className="mt-2 text-slate-500">{subtitle}</p></div>{action}</header>}
