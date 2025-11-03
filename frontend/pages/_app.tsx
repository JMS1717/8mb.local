import type { AppProps } from "next/app";
import "../styles/globals.css";

export default function EightMbLocalApp({ Component, pageProps }: AppProps) {
  return <Component {...pageProps} />;
}
