package com.sense2act.backend.common;

import java.util.List;

/** 分页响应 data 结构 {items, total, page, page_size},见 docs/api-design.md §1。 */
public record PageResult<T>(List<T> items, long total, int page, int page_size) {

    public static <T> PageResult<T> of(List<T> items, long total, PageParams params) {
        return new PageResult<>(items, total, params.page(), params.pageSize());
    }
}
