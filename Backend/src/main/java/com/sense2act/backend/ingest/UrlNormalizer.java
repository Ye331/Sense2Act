package com.sense2act.backend.ingest;

import java.net.URI;

/**
 * URL 归一化,入库前统一:去空白、去 fragment、scheme/host 小写、去尾部斜杠。
 * 查询串保留(可能带翻页/详情参数),路径大小写保留。
 * 解析失败时返回去空白原文,交由后续长度/唯一约束兜底。
 */
public final class UrlNormalizer {

    private UrlNormalizer() {
    }

    public static String normalize(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String s = raw.trim();
        int frag = s.indexOf('#');
        if (frag >= 0) {
            s = s.substring(0, frag);
        }
        try {
            URI uri = URI.create(s);
            String scheme = uri.getScheme();
            String authority = uri.getAuthority();
            if (scheme == null || authority == null) {
                return s;
            }
            String path = uri.getPath() == null ? "" : uri.getPath();
            while (path.length() > 1 && path.endsWith("/")) {
                path = path.substring(0, path.length() - 1);
            }
            String query = uri.getQuery();
            return (scheme + "://" + authority).toLowerCase() + path + (query != null ? "?" + query : "");
        } catch (IllegalArgumentException e) {
            return s;
        }
    }
}
