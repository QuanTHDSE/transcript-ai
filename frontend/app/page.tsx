import Link from "next/link";
export default function Home() { return <div className="card"><h1 className="text-2xl font-semibold">Transcripto AI</h1><p className="my-4">Upload media and follow your transcription queue.</p><Link href="/upload">Upload files →</Link><br/><Link href="/jobs">View queue →</Link></div>; }
