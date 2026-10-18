package com.sense2act.backend.common;

/** 分页参数:page 从 1 起,page_size 上限 100(决策 D1)。 */
public record PageParams(int page, int pageSize) {

    public static final int MAX_PAGE_SIZE = 100;
    public static final int DEFAULT_PAGE_SIZE = 20;

    public static PageParams of(Integer page, Integer pageSize) {
        int p = page == null ? 1 : page;
        int ps = pageSize == null ? DEFAULT_PAGE_SIZE : pageSize;
        if (p < 1 || ps < 1) {
            throw BusinessException.badRequest("page 与 page_size 必须为正整数");
        }
        if (ps > MAX_PAGE_SIZE) {
            throw BusinessException.badRequest("page_size 上限为 " + MAX_PAGE_SIZE);
        }
        return new PageParams(p, ps);
    }

    public long offset() {
        return (long) (page - 1) * pageSize;
    }
}
