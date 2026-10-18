// Text that would break out of an attribute or a text node if it were not escaped.
export const evils = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "a&b'c", 'x" onmouseover="alert(1)'];
export const decode = (t) => t.replace(/&#(\d+);/g, (_, n) => String.fromCharCode(n));
export const escaped = /^(?:[^<>"'&]|&#\d+;)*$/;
