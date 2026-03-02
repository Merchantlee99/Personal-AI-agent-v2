import { ChatDashboard } from "@/components/chat-dashboard";

export default function Home() {
  return (
    <div style={{
      /* 전체 페이지 배경 */
      background: 'linear-gradient(135deg, #0B1120 0%, #0F1A2E 40%, #111D32 70%, #0B1120 100%)',
      minHeight: '100vh',
      margin: 0,
      padding: 0,
    }}>
      <ChatDashboard />
    </div>
  );
}
