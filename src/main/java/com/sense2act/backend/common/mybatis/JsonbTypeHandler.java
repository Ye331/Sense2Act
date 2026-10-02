package com.sense2act.backend.common.mybatis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ibatis.type.BaseTypeHandler;
import org.apache.ibatis.type.JdbcType;
import org.apache.ibatis.type.MappedTypes;

import java.sql.CallableStatement;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;

/**
 * PG JSONB ↔ Java Map/List。写入用 setObject(Types.OTHER) 让驱动按服务端类型收,
 * 避免默认 setString 触发 "character varying 不能进 jsonb" 的报错。
 */
@MappedTypes(Object.class)
public class JsonbTypeHandler extends BaseTypeHandler<Object> {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Override
    public void setNonNullParameter(PreparedStatement ps, int i, Object parameter, JdbcType jdbcType)
            throws SQLException {
        try {
            ps.setObject(i, MAPPER.writeValueAsString(parameter), Types.OTHER);
        } catch (Exception e) {
            throw new SQLException("JSON 序列化失败", e);
        }
    }

    @Override
    public Object getNullableResult(ResultSet rs, String columnName) throws SQLException {
        return parse(rs.getString(columnName));
    }

    @Override
    public Object getNullableResult(ResultSet rs, int columnIndex) throws SQLException {
        return parse(rs.getString(columnIndex));
    }

    @Override
    public Object getNullableResult(CallableStatement cs, int columnIndex) throws SQLException {
        return parse(cs.getString(columnIndex));
    }

    private Object parse(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return MAPPER.readValue(json, Object.class);
        } catch (Exception e) {
            throw new IllegalStateException("JSON 反序列化失败: " + json.substring(0, Math.min(100, json.length())), e);
        }
    }
}
