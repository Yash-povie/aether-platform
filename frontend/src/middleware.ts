import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Public paths: login page and API routes (including health check)
  if (pathname.startsWith('/login') || pathname.startsWith('/api/')) {
    return NextResponse.next();
  }

  const token = request.cookies.get('aether_token')?.value;
  if (!token) {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};