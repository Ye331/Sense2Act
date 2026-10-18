package com.sense2act.backend.domain.user;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.ErrorCode;
import org.springframework.stereotype.Service;

/** 用户查询。 */
@Service
public class UserService {

    private final UserMapper userMapper;

    public UserService(UserMapper userMapper) {
        this.userMapper = userMapper;
    }

    public User findByEmail(String email) {
        return userMapper.selectOne(new LambdaQueryWrapper<User>().eq(User::getEmail, email));
    }

    public User requireById(String id) {
        User user = userMapper.selectById(id);
        if (user == null) {
            throw new BusinessException(ErrorCode.NOT_FOUND, "用户不存在");
        }
        return user;
    }
}
