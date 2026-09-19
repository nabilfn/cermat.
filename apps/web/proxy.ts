import { NextResponse, type NextRequest } from "next/server";

// Fast redirect for signed-out visitors. This is a convenience only — every
// API request is authenticated and authorised by FastAPI.
export function proxy(request: NextRequest) {
  if (!request.cookies.has("cermat_session")) {
    const url = request.nextUrl.clone();
    url.pathname = "/signin";
    url.search = `?next=${encodeURIComponent(request.nextUrl.pathname)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/workspace/:path*"] };
