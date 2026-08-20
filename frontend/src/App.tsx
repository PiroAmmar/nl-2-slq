// src/App.tsx
import { BrowserRouter, Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Upload from "./pages/Upload";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import { ThemeProvider } from "./components/ThemeProvider";
import { ThemeToggle } from "./components/ThemeToggle";
import { useDatasetStore } from "./state/datasetStore";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 2, staleTime: 30_000 },
  },
});

function AppLayout() {
  const location = useLocation();
  const isUpload = location.pathname === '/';
  const { datasetId, datasetName, clearDataset } = useDatasetStore();
  
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {!isUpload && (
        <header style={{ 
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', 
          padding: '12px 24px', 
          borderBottom: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-elevated)',
          position: 'sticky', top: 0, zIndex: 10
        }}>
          <div style={{ display: 'flex', gap: '24px', alignItems: 'center' }}>
            <h1 style={{ fontSize: '16px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
              <img src="/logo.svg" alt="Logo" style={{ height: '40px', marginRight: '8px' }} /> 
              NL-to-SQL
              {datasetName && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>/ {datasetName}</span>}
            </h1>
            <nav style={{ display: 'flex', gap: '16px' }}>
              <Link to="/chat" style={{ color: location.pathname === '/chat' ? 'var(--accent-base)' : 'var(--text-muted)', textDecoration: 'none', fontSize: '14px', fontWeight: 500, transition: 'color 0.2s' }}>Chat</Link>
              <Link to="/dashboard" style={{ color: location.pathname === '/dashboard' ? 'var(--accent-base)' : 'var(--text-muted)', textDecoration: 'none', fontSize: '14px', fontWeight: 500, transition: 'color 0.2s' }}>Dashboard</Link>
            </nav>
          </div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <button 
              onClick={clearDataset} 
              style={{
                background: 'transparent', border: '1px solid var(--border-subtle)', 
                color: 'var(--text-main)', borderRadius: '6px', padding: '6px 12px', 
                cursor: 'pointer', fontSize: '13px', fontWeight: 500
              }}
            >
              Change Dataset
            </button>
            <ThemeToggle />
          </div>
        </header>
      )}
      {isUpload && (
        <div style={{ position: 'absolute', top: 24, right: 24, zIndex: 10 }}>
           <ThemeToggle />
        </div>
      )}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Routes>
          <Route path="/" element={<Upload />} />
          <Route path="/chat" element={datasetId ? <Chat /> : <Navigate to="/" replace />} />
          <Route path="/dashboard" element={datasetId ? <Dashboard /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AppLayout />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
