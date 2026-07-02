export async function GET() {
  return Response.json({
    status: 'ok',
    service: 'aether-frontend',
    timestamp: new Date().toISOString(),
  });
}