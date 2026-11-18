import type { ReactNode } from "react";
import "./globals.css";
import { Shell } from "../components/shell";
export const metadata = { title: "Transcripto AI", description: "Local transcription workspace" };
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) { return <html lang="en"><body><Shell>{children}</Shell></body></html>; }
