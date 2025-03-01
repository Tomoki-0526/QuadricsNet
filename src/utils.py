import os
import numpy as np
from src.guard import guard_sqrt
import torch
import torch.nn.functional as F
import open3d as o3d
from open3d import *
Vector3dVector, Vector3iVector = utility.Vector3dVector, utility.Vector3iVector
draw_geometries = o3d.visualization.draw_geometries

def get_rotation_matrix(theta):
    R = np.array([[np.cos(theta), np.sin(theta), 0],
                  [-np.sin(theta), np.cos(theta), 0],
                  [0, 0, 1]])
    return R


def rotation_matrix_a_to_b(A, B):
    """
    Finds rotation matrix from vector A in 3d to vector B
    in 3d.
    B = R @ A
    """
    EPS = 1e-8
    cos = np.dot(A, B)
    sin = np.linalg.norm(np.cross(B, A))
    u = A
    v = B - np.dot(A, B) * A
    v = v / (np.linalg.norm(v) + EPS)
    w = np.cross(B, A)
    w = w / (np.linalg.norm(w) + EPS)
    F = np.stack([u, v, w], 1)
    G = np.array([[cos, -sin, 0],
                  [sin, cos, 0],
                  [0, 0, 1]])
    # B = R @ A
    try:
        R = F @ G @ np.linalg.inv(F)
    except:
        R = np.eye(3, dtype=np.float32)
    return R


def rescale_input_outputs(scales, points, output, batch_size):

    scales = np.stack(scales, 0).astype(np.float32)
    scales = torch.from_numpy(scales).cuda()

    output = (
            output
            / (scales.reshape((batch_size, 1)) ** 2)
    )

    points = (
            points
            * scales.reshape((batch_size, 1, 1))
    )

    return scales, points, output

def rescale_input_outputs_quadrics(T_batch, scale_quadrics,quadrics, output,batch_size):

    T_batch = np.stack(T_batch, 0).astype(np.float32)
    T_batch = torch.from_numpy(T_batch).cuda()
    scale_quadrics = torch.from_numpy(scale_quadrics).cuda()

    quadrics = quadrics.squeeze(2)

    quadrics_out = output

    for i in range(batch_size):

        # quadrics[i] = quadrics[i] * scale_quadrics[i]
        # quadrics_out[i] = quadrics_out[i] * scale_quadrics[i]

        # 杜绝inplace操作
        Q_quadrics = q_Q(quadrics[i] * scale_quadrics[i]).cuda()
        Q_output = q_Q(quadrics_out[i] * scale_quadrics[i]).cuda()

        Q_quadrics = torch.matmul(torch.matmul(T_batch[i].T,Q_quadrics),T_batch[i])
        Q_output = torch.matmul(torch.matmul(T_batch[i].T,Q_output),T_batch[i])

        quadrics_d_T_each = torch.tensor([[Q_quadrics[0, 0]], [Q_quadrics[1, 1]], [Q_quadrics[2, 2]], [Q_quadrics[0, 1]], [Q_quadrics[0, 2]]
                                , [Q_quadrics[1, 2]], [Q_quadrics[0, 3]], [Q_quadrics[1, 3]], [Q_quadrics[2, 3]],
                             [Q_quadrics[3, 3]]])
        output_d_T_each = torch.tensor([[Q_output[0, 0]], [Q_output[1, 1]], [Q_output[2, 2]], [Q_output[0, 1]], [Q_output[0, 2]]
                , [Q_output[1, 2]], [Q_output[0, 3]], [Q_output[1, 3]], [Q_output[2, 3]],
             [Q_output[3, 3]]])

        if i == 0:
            quadrics_d_T = quadrics_d_T_each.unsqueeze(0)
            output_d_T = output_d_T_each.unsqueeze(0)
        else:
            quadrics_d_T = torch.cat((quadrics_d_T,quadrics_d_T_each.unsqueeze(0)),0)
            output_d_T = torch.cat((output_d_T,output_d_T_each.unsqueeze(0)),0)
    return quadrics_d_T, output_d_T


def grad_norm(model):
    total_norm = 0
    for p in model.parameters():
        param_norm = p.grad.data.norm(2)
        total_norm += param_norm
    total_norm = total_norm.item()
    return np.isnan(total_norm) or np.isinf(total_norm)

def q_Q(q):
    Q = torch.tensor([[q[0], q[3], q[4], q[6]],
                        [q[3], q[1], q[5], q[7]],
                        [q[4], q[5], q[2], q[8]],
                        [q[6], q[7], q[8], q[9]]]).cuda(q.device)
    return Q

def q_Q_numpy(q):
    Q = np.array([[q[0], q[3], q[4], q[6]],
                [q[3], q[1], q[5], q[7]],
                [q[4], q[5], q[2], q[8]],
                [q[6], q[7], q[8], q[9]]])
    return Q

def Q_q(Q):
    q = torch.tensor([Q[0, 0], Q[1, 1], Q[2, 2], Q[0, 1], Q[0, 2], Q[1, 2], Q[0, 3], Q[1, 3], Q[2, 3],Q[3, 3]]).cuda(Q.device)
    return q

def quadrics_reg_distance(output, quadrics):
    q = output

    # q = torch.unsqueeze(q, 2)

    # N x 8 x grid_size x grid_size x 3
    distance_quadrics_reg = (q - quadrics) ** 2
    distance_quadrics_reg = torch.mean(distance_quadrics_reg)

    return distance_quadrics_reg

def quadrics_decomposition_distance(output, quadrics,trans_inv,C,eval=False,shape=''):
    q_gt = quadrics
    q_pre = output

    C_pre = C
    if eval:
        if shape in ["sphere","ellipsoid","cylinder","elliptic_cylinder"]:
            Q_gt = torch.tensor([[q_gt[0], q_gt[3], q_gt[4], q_gt[6]],
                                    [q_gt[3], q_gt[1], q_gt[5], q_gt[7]],
                                    [q_gt[4], q_gt[5], q_gt[2], q_gt[8]],
                                    [q_gt[6], q_gt[7], q_gt[8], q_gt[9]]]).cuda(q_gt.device)
            Q_pre = torch.tensor([[q_pre[0], q_pre[3], q_pre[4], q_pre[6]],
                                    [q_pre[3], q_pre[1], q_pre[5], q_pre[7]],
                                    [q_pre[4], q_pre[5], q_pre[2], q_pre[8]],
                                    [q_pre[6], q_pre[7], q_pre[8], q_pre[9]]]).cuda(q_pre.device)
            Q_gt,_ = quadrics_scale_identification(Q_gt,shape)
            Q_pre,_ = quadrics_scale_identification(Q_pre,shape)
        
        elif shape in ["plane","cone","elliptic_cone"]:

            q_gt_ = F.normalize(q_gt,p=2,dim=0)
            q_pre_ = F.normalize(q_pre,p=2,dim=0)

            Q_gt = torch.tensor([[q_gt_[0], q_gt_[3], q_gt_[4], q_gt_[6]],
                                    [q_gt_[3], q_gt_[1], q_gt_[5], q_gt_[7]],
                                    [q_gt_[4], q_gt_[5], q_gt_[2], q_gt_[8]],
                                    [q_gt_[6], q_gt_[7], q_gt_[8], q_gt_[9]]]).cuda(q_gt.device)
            Q_pre = torch.tensor([[q_pre_[0], q_pre_[3], q_pre_[4], q_pre_[6]],
                                    [q_pre_[3], q_pre_[1], q_pre_[5], q_pre_[7]],
                                    [q_pre_[4], q_pre_[5], q_pre_[2], q_pre_[8]],
                                    [q_pre_[6], q_pre_[7], q_pre_[8], q_pre_[9]]]).cuda(q_pre.device)

    elif not eval:
        if shape in ["sphere","ellipsoid","cylinder","elliptic_cylinder"]:
            Q_gt = torch.tensor([[q_gt[0], q_gt[3], q_gt[4], q_gt[6]],
                                    [q_gt[3], q_gt[1], q_gt[5], q_gt[7]],
                                    [q_gt[4], q_gt[5], q_gt[2], q_gt[8]],
                                    [q_gt[6], q_gt[7], q_gt[8], q_gt[9]]]).cuda(q_gt.device)
            _,scale_identification_gt = quadrics_scale_identification(Q_gt,shape)
            Is_add = 1
        elif shape in ["plane","cone","elliptic_cone"]:
            Q_gt = torch.tensor([[q_gt[0], q_gt[3], q_gt[4], q_gt[6]],
                                    [q_gt[3], q_gt[1], q_gt[5], q_gt[7]],
                                    [q_gt[4], q_gt[5], q_gt[2], q_gt[8]],
                                    [q_gt[6], q_gt[7], q_gt[8], q_gt[9]]]).cuda(q_gt.device)
            scale_identification_gt = 0
            Is_add = 0

    ###########################
    # trans_inv=[R.T, -R.T*t
    #             0  ,  1  ]
    trans_t_ = trans_inv[0:3,3] # -R.T*t
    trans_r = trans_inv[0:3,0:3].transpose(0,1)
    trans_t = -torch.matmul(trans_r,trans_t_)

    # upper 3x3 matrix  
    E_gt = Q_gt[0:3,0:3]

    ###########################
    # gt eigen
    # get the eigenvalue and eigenvector and sort they in the descend order.
    value_gt,vector_gt = torch.eig(E_gt, eigenvectors=True)
    value_gt_sorted,idx_gt = torch.sort(value_gt[:,0],descending=True)
    Is_gt,Ir_gt,It_gt = quadrics_judgment(value_gt_sorted)
    vector_gt_sorted = vector_gt[:,idx_gt]
    scale_gt_sorted = torch.sqrt(1 / ((Is_gt * value_gt_sorted) + 1e-8))
    scale_gt_sorted = torch.diag_embed(scale_gt_sorted).cuda(q_gt.device)
    value_gt_sorted = torch.diag_embed(value_gt_sorted).cuda(q_gt.device)

    ###########################
    # pre eigen
    if not eval:
        value_pre_sorted,idx_pre = torch.sort(torch.diag(C_pre)[0:3],descending=True)
        vector_pre_sorted = trans_r[:,idx_pre]

        scale_pre_sorted = torch.sqrt(1 / ((Is_gt * value_pre_sorted)+ 1e-8))
    elif eval:
        E_pre = Q_pre[0:3,0:3]
        value_pre,vector_pre = torch.eig(E_pre, eigenvectors=True)
        value_pre_sorted,idx_pre = torch.sort(value_pre[:,0],descending=True)
        vector_pre_sorted = vector_pre[:,idx_pre]

        scale_pre_sorted = torch.sqrt(torch.abs(1 / ((Is_gt * value_pre_sorted)+ 1e-8)))
    
    scale_pre_sorted = torch.diag_embed(scale_pre_sorted).cuda(q_gt.device)
    value_pre_sorted = torch.diag_embed(value_pre_sorted).cuda(q_gt.device)

    ###########################
    # if (shape in ["cone","elliptic_cone"]) and eval:
    #     if sum(torch.diag(value_gt_sorted) < 0) == 1:
    #         factor_gt = -value_gt_sorted[value_gt_sorted < 0]
    #         value_gt_sorted = value_gt_sorted / factor_gt
    #         scale_gt_sorted = scale_gt_sorted * torch.sqrt(factor_gt)
    #         Q_gt = Q_gt / factor_gt

    #     if sum(torch.diag(value_pre_sorted) < 0) == 1:
    #         factor_pre = -value_pre_sorted[value_pre_sorted < 0]
    #         value_pre_sorted = value_pre_sorted / factor_pre
    #         scale_pre_sorted = scale_pre_sorted * torch.sqrt(factor_pre)
    #         Q_pre = Q_pre / factor_pre
    #         trans_t = trans_t / factor_pre

    if (shape in ["cone","elliptic_cone"]) and eval:
        if sum(torch.diag(value_gt_sorted) < 0) == 1:
            factor_gt = -value_gt_sorted[value_gt_sorted < 0]
            scale_gt_sorted = scale_gt_sorted * torch.sqrt(factor_gt)

        if sum(torch.diag(value_pre_sorted) < 0) == 1:
            factor_pre = -value_pre_sorted[value_pre_sorted < 0]
            scale_pre_sorted = scale_pre_sorted * torch.sqrt(factor_pre)

    ###########################
    # ldr
    if torch.sum(Ir_gt) == 0:
        # sphere 
        loss_decomposition_r = torch.sum(Ir_gt)
    else:
        # sum(((vector_gt x vector_pre) * I_r_gt)^2) / (sum(I_r_gt)*3)
        loss_decomposition_r = torch.sum((torch.matmul(torch.cross(vector_gt_sorted, vector_pre_sorted,dim=0),torch.diag_embed(Ir_gt)) **2))/(torch.sum(Ir_gt)*3)

    ###########################
    # lds
    # sum(((value_gt - value_pre) * I_r_gt)^2) / (sum(I_r_gt))
    if torch.sum(Is_gt) == 0:
        # plane [0 0 0 0]
        loss_decomposition_s = torch.sum(Is_gt)
    else:
        if not eval:
            loss_decomposition_s = torch.sum(torch.abs(value_gt_sorted - value_pre_sorted))
            loss_decomposition_s = loss_decomposition_s + torch.abs(C_pre[3,3] + scale_identification_gt)
            loss_decomposition_s = loss_decomposition_s/(torch.count_nonzero(value_gt_sorted)+Is_add)
        elif eval:
            loss_decomposition_s = torch.sum((torch.matmul((scale_gt_sorted - scale_pre_sorted),torch.diag_embed(Is_gt))**2))/torch.sum(Is_gt)

    ###########################
    # ldt
    # lamb * v.T * t +  v.T * l   (It=1)
    if torch.sum(It_gt) == 0:
        loss_decomposition_t = torch.sum(It_gt)
    else:
        loss_decomposition_t = torch.sum(torch.matmul(torch.matmul(torch.matmul(value_gt_sorted,vector_gt_sorted.transpose(1,0)),trans_t) + torch.matmul(vector_gt_sorted.transpose(1,0),Q_gt[0:3,3]),torch.diag_embed(It_gt))**2)/torch.sum(It_gt)
    
    ###########################

    return loss_decomposition_r,loss_decomposition_s,loss_decomposition_t

def quadrics_scale_identification(Q,shape):
    eigenvalue_Q,_ = torch.eig(Q,eigenvectors=False)
    eigenvalue_Q = eigenvalue_Q[:,0]

    if shape in ["cylinder","elliptic_cylinder"]:
        min_abs_index = torch.argmin(torch.abs(eigenvalue_Q))
        # 删除该元素
        eigenvalue_Q = torch.cat((eigenvalue_Q[:min_abs_index], eigenvalue_Q[min_abs_index + 1:]))

    scale_Q = torch.tensor([1]).cuda(Q.device)
    for i in eigenvalue_Q:
        scale_Q = scale_Q * i

    eigenvalue_E,_ = torch.eig(Q[0:3,0:3],eigenvectors=False)
    eigenvalue_E = eigenvalue_E[:,0]

    if shape in ["cylinder","elliptic_cylinder"]:
        # eigenvalue_E = eigenvalue_E[np.where(np.abs(eigenvalue_E) > (eigenvalue_E_sum * 0.01))]
        # 找到绝对值最小的元素的索引
        min_abs_index = torch.argmin(torch.abs(eigenvalue_E))
        # 删除该元素
        eigenvalue_E = torch.cat((eigenvalue_E[:min_abs_index], eigenvalue_E[min_abs_index + 1:]))

    scale_E = torch.tensor([1]).cuda(Q.device)

    for i in eigenvalue_E:
        scale_E = scale_E * i
    
    scale_identification = torch.abs(scale_E / scale_Q)
    Q = scale_identification * Q
    return Q,np.squeeze(1/scale_identification)

def quadrics_function_distance(output, points):
    q = output

    Q = torch.tensor([[q[0], q[3], q[4], q[6]],
                                [q[3], q[1], q[5], q[7]],
                                [q[4], q[5], q[2], q[8]],
                                [q[6], q[7], q[8], q[9]]]).cuda(device=points.device)

    append_one = torch.ones(points.size(0), 1).cuda(device=points.device)
    append_one = append_one.cuda(device=points.device)
    points_append = torch.cat((points, append_one), 1)


    # X_reshaped = points_append.view(points_append.shape[0], 1, points_append.shape[1])  # 调整为 (10000, 1, 4)
    # XQ = torch.bmm(X_reshaped, Q.unsqueeze(0).expand(X_reshaped.size(0), *Q.size()))
    # X_transpose = points_append.view(points_append.shape[0], points_append.shape[1], 1)  # 调整为 (10000, 4, 1)
    # result = torch.bmm(XQ, X_transpose)
    # result = result.squeeze(2)

    distance_quadrics_function = torch.mean(torch.einsum('ij,jk,ki->i', points_append, Q, points_append.T).pow(2))

    # # distance_quadrics_function = mean(x*Q*xT)
    # distance_quadrics_function = torch.matmul(torch.matmul(points_append, Q), points_append.transpose(1, 0)).pow(2)
    # distance_quadrics_function = torch.mean(distance_quadrics_function)

    return distance_quadrics_function


def taubin_distance(output, points):
    q = output

    Q = torch.tensor([[q[0], q[3], q[4], q[6]],
                                [q[3], q[1], q[5], q[7]],
                                [q[4], q[5], q[2], q[8]],
                                [q[6], q[7], q[8], q[9]]]).cuda(device=points.device)

    append_one = torch.ones(points.size(0), 1)
    append_one = append_one.cuda(points.device)
    points_append = torch.cat((points, append_one), 1)

    # x*Q*xT
    quadrics_function_each = torch.einsum('ij,jk,ki->i', points_append, Q, points_append.T).pow(2)
    deta_function_each = torch.norm(compute_normals_analytically_torch(points,q,if_normalize=False),dim=1,p=2).pow(2)
    distance_taubin = quadrics_function_each / (deta_function_each + 1e-8)

    distance_taubin = torch.mean(distance_taubin)

    return distance_taubin
    
def normals_deviation_distance(output,points,normals,quadrics):

    normals_analytical = compute_normals_analytically_torch(points,output)
    loss_normals_deviation = torch.mean(torch.abs(torch.cross(normals_analytical,normals)))

    return loss_normals_deviation

def compute_normals_analytically_torch(points_temp,quadrics_temp,if_normalize=True):
    untils_zeros = torch.zeros([points_temp.shape[0],1]).cuda(device=points_temp.device)
    untils_ones = torch.ones([points_temp.shape[0],1]).cuda(device=points_temp.device)
    untils_points_x = torch.unsqueeze(points_temp[:,0],axis=-1)
    untils_points_y = torch.unsqueeze(points_temp[:,1],axis=-1)
    untils_points_z = torch.unsqueeze(points_temp[:,2],axis=-1)

    deta_v_0 = torch.cat((2*untils_points_x,untils_zeros,untils_zeros,2*untils_points_y,2*untils_points_z,untils_zeros,2*untils_ones,untils_zeros,untils_zeros,untils_zeros),axis=1)
    deta_v_1 = torch.cat((untils_zeros,2*untils_points_y,untils_zeros,2*untils_points_x,untils_zeros,2*untils_points_z,untils_zeros,2*untils_ones,untils_zeros,untils_zeros),axis=1)
    deta_v_2 = torch.cat((untils_zeros,untils_zeros,2*untils_points_z,untils_zeros,2*untils_points_x,2*untils_points_y,untils_zeros,untils_zeros,2*untils_ones,untils_zeros),axis=1)

    deta_v_0 = torch.unsqueeze(deta_v_0,axis=1)
    deta_v_1 = torch.unsqueeze(deta_v_1,axis=1)
    deta_v_2 = torch.unsqueeze(deta_v_2,axis=1)

    deta_v = torch.cat((deta_v_0,deta_v_1,deta_v_2),axis=1)
    normlas_temp = torch.squeeze(torch.matmul(deta_v,torch.unsqueeze(quadrics_temp,axis=-1)),2)

    if if_normalize:
        normlas_temp = torch.nn.functional.normalize(normlas_temp,p=2, dim=1)

    return normlas_temp


def rescale_input_outputs_quadrics_e2e(T_batch,T_batch_sample, scale_quadrics_batch_sample,quadrics_gt_batch, quadrics_pre_batch, batch_size):
    quadrics_pre_rescale_batch = []
    quadrics_gt_rescale_batch = []
    for i in range(batch_size):
        T_sample = torch.stack(T_batch_sample[i],0).float()
        quadrics_gt = torch.stack(quadrics_gt_batch[i],0).float()
        quadrics_pre = torch.stack(quadrics_pre_batch[i],0).float()
        T = torch.from_numpy(T_batch[i]).cuda(T_sample.device).float()
        scale_quadrics_sample = torch.stack(scale_quadrics_batch_sample[i],0).float()

        quadrics_pre_rescale_sample = []
        quadrics_gt_rescale_sample = []
        for j,_ in enumerate(quadrics_pre):
            Q_pre = q_Q(quadrics_pre[j] * scale_quadrics_sample[j])
            Q_gt = q_Q(quadrics_gt[j] * scale_quadrics_sample[j])

            # Q_pre = Q_pre * scale_quadrics_sample[j]
            Q_pre = torch.matmul(torch.matmul(T_sample[j].T,Q_pre),T_sample[j])
            Q_pre = torch.matmul(torch.matmul(T.T,Q_pre),T)

            Q_gt = torch.matmul(torch.matmul(T_sample[j].T,Q_gt),T_sample[j])
            Q_gt = torch.matmul(torch.matmul(T.T,Q_gt),T)

            quadrics_pre_rescale_sample.append(Q_q(Q_pre).data.cpu().numpy())
            quadrics_gt_rescale_sample.append(Q_q(Q_gt).data.cpu().numpy())

        quadrics_pre_rescale_batch.append(quadrics_pre_rescale_sample)
        quadrics_gt_rescale_batch.append(quadrics_gt_rescale_sample)

    return quadrics_pre_rescale_batch,quadrics_gt_rescale_batch

def quadrics_judgment(eigenvalue):

    margin_0 = 1e-3
    margin_1 = 1e-2

    x = eigenvalue[1]/eigenvalue[0]
    y = eigenvalue[2]/eigenvalue[0]

    # translation degeneration
    It = (torch.abs(eigenvalue)>margin_0).float().cuda(eigenvalue.device)

    # scale degeneration
    Is = It

    # in case of plane [1 0 0 0]
    if torch.abs(x) < margin_0 and torch.abs(y) < margin_0:
        Is = torch.tensor([0,0,0]).float().cuda(eigenvalue.device)
    # in case of cylinder [1 1 0 -1]
    if x > margin_0 and torch.abs(y) < margin_0:
        Is = torch.tensor([1,1,0]).float().cuda(eigenvalue.device)
    # in case of cone [1 1 -1 0]
    if x > margin_0 and y < -margin_0:
        Is = torch.tensor([1,1,0]).float().cuda(eigenvalue.device)

    # rotation degeneration
    Ir = torch.ones(3).cuda(eigenvalue.device)

    if torch.abs(x - 1) < margin_1:
        Ir[1] = 0
        Ir[0] = 0
    if torch.abs(x - y) < margin_1:
        Ir[1] = 0
        Ir[2] = 0
    
    return Is,Ir,It

def quadrics_scale_identification_pytorch(Q):
    eigenvalue_Q,_ = torch.eig(Q,eigenvectors=False)
    eigenvalue_Q = eigenvalue_Q[:,0]

    eigenvalue_Q_sum = torch.sum(torch.abs(eigenvalue_Q))
    eigenvalue_Q = eigenvalue_Q[torch.where(torch.abs(eigenvalue_Q) > (eigenvalue_Q_sum * 0.01))]

    scale_Q = torch.tensor([1]).cuda(Q.device)
    for i in eigenvalue_Q:
        scale_Q = scale_Q * i

    eigenvalue_E,_ = torch.eig(Q[0:3,0:3],eigenvectors=False)
    eigenvalue_E = eigenvalue_E[:,0]

    eigenvalue_E_sum = torch.sum(torch.abs(eigenvalue_E))
    eigenvalue_E = eigenvalue_E[torch.where(torch.abs(eigenvalue_E) > (eigenvalue_E_sum * 0.05))]
    scale_E = torch.tensor([1]).cuda(Q.device)

    for i in eigenvalue_E:
        scale_E = scale_E * i
    
    scale_identification = torch.abs(scale_E / scale_Q)
    Q = scale_identification * Q
    return Q,torch.squeeze(1/scale_identification)

def visualize_point_cloud(points, normals=[], colors=[], file="", viz=False):
    # pcd = PointCloud()
    pcd = geometry.PointCloud()
    pcd.points = Vector3dVector(points)

    # estimate_normals(pcd, search_param = KDTreeSearchParamHybrid(
    #         radius = 0.1, max_nn = 30))
    if isinstance(normals, np.ndarray):
        pcd.normals = Vector3dVector(normals)
    if isinstance(colors, np.ndarray):
        pcd.colors = Vector3dVector(colors)

    if file:
        write_point_cloud(file, pcd, write_ascii=True)

    if viz:
        draw_geometries([pcd])
    return pcd


